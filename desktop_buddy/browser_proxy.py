"""Private browser proxy enforcing service routing before opening an upstream socket.

HTTPS is an opaque CONNECT tunnel: no certificate replacement, TLS inspection,
credential storage, or changes to the user's system proxy settings.
"""
import asyncio
import threading
from urllib.parse import urlsplit


class RoutingBlocked(Exception):
    pass


class BrowserProxy:
    def __init__(self,guard,on_block):
        self.guard=guard;self.on_block=on_block;self.ready=threading.Event()
        self.loop=asyncio.new_event_loop();self.clients=set();self.error=None
        self.close_lock=threading.Lock();self.closing=False
        self.thread=threading.Thread(target=self._run,daemon=True,name='buddy-browser-proxy')
        self.thread.start()
        if not self.ready.wait(5):
            self.close();raise RuntimeError('Browser routing could not start.')
        if self.error:raise RuntimeError('Browser routing could not start.') from self.error

    def _run(self):
        asyncio.set_event_loop(self.loop)
        server=None
        try:
            server=self.loop.run_until_complete(asyncio.start_server(self._client,'127.0.0.1',0,limit=65536))
            self.port=server.sockets[0].getsockname()[1]
            self.ready.set();self.loop.run_forever()
        except Exception as error:self.error=error;self.ready.set()
        finally:
            if server:server.close()
            # On current Python, wait_closed waits for accepted connections too.
            # Stop their reader tasks first so idle tunnels cannot deadlock exit.
            tasks=asyncio.all_tasks(self.loop)
            for task in tasks:task.cancel()
            if tasks:self.loop.run_until_complete(asyncio.gather(*tasks,return_exceptions=True))
            if server:self.loop.run_until_complete(server.wait_closed())
            self.loop.close()

    def close(self):
        # Chrome's exit watcher and the UI may close concurrently. A second
        # loop.stop during run_until_complete would interrupt task cleanup.
        with self.close_lock:
            if not self.closing:
                self.closing=True
                if self.thread.is_alive():
                    try:self.loop.call_soon_threadsafe(self.loop.stop)
                    except RuntimeError:pass
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(3)

    def check(self,url):
        try:self.guard(url)
        except Exception as error:
            self.on_block(urlsplit(url).hostname,str(error))
            raise RoutingBlocked from error

    @staticmethod
    async def reply(writer,status,message):
        body=message.encode('utf-8')
        writer.write(f'HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()+body)
        await writer.drain()

    @staticmethod
    def destination(method,target):
        parsed=urlsplit(('https://' if method=='CONNECT' else '')+target)
        if (parsed.scheme not in ('http','https','ws') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise ValueError('Invalid browser destination.')
        if method=='CONNECT' and (parsed.path or parsed.query):raise ValueError('Invalid browser tunnel.')
        if method!='CONNECT' and parsed.scheme=='https':raise ValueError('HTTPS requires a tunnel.')
        port=parsed.port or (443 if method=='CONNECT' else 80)
        if not 1<=port<=65535:raise ValueError('Invalid browser port.')
        return parsed,port

    @staticmethod
    def request_headers(method,parsed,version,lines):
        if version not in ('HTTP/1.0','HTTP/1.1'):raise ValueError('Unsupported browser request.')
        headers=[];has_length=False;has_encoding=False;upgrade=False
        for line in lines:
            key,sep,value=line.partition(b':')
            if not sep or not key or any(c not in b"!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for c in key):
                raise ValueError('Invalid browser header.')
            if b'\x00' in value:raise ValueError('Invalid browser header.')
            name=key.lower();value=value.strip()
            if name==b'content-length':
                if has_length or not value.isdigit():raise ValueError('Invalid browser body length.')
                has_length=True
            if name==b'transfer-encoding':has_encoding=True
            if name==b'upgrade' and value.lower()==b'websocket':upgrade=True
            if name not in (b'host',b'connection') and not name.startswith(b'proxy-'):
                headers.append(key+b': '+value)
        if has_length and has_encoding:raise ValueError('Ambiguous browser body length.')
        path=parsed.path or '/'
        if parsed.query:path+='?'+parsed.query
        headers.insert(0,f'{method} {path} {version}'.encode('ascii'))
        headers.append(b'Host: '+parsed.netloc.encode('ascii'))
        # One HTTP request per connection keeps each destination subject to a new
        # check. WebSocket upgrades retain their ordinary bidirectional stream.
        headers.append(b'Connection: Upgrade' if upgrade else b'Connection: close')
        return b'\r\n'.join(headers)+b'\r\n\r\n'

    @staticmethod
    async def pump(reader,writer,check):
        while data:=await asyncio.wait_for(reader.read(65536),timeout=60):
            # An IT-managed service mapping can change while a TLS tunnel is
            # already open. Recheck before forwarding subsequent bytes as well.
            check()
            writer.write(data);await asyncio.wait_for(writer.drain(),timeout=30)
        if writer.can_write_eof():writer.write_eof()

    async def _client(self,reader,writer):
        task=asyncio.current_task();upstream=None;pumps=[]
        if len(self.clients)>=128:
            writer.close();return
        self.clients.add(task)
        try:
            raw=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),timeout=10)
            lines=raw[:-4].split(b'\r\n')
            method,target,version=lines[0].decode('ascii').split(' ')
            if not method.isalpha() or method!=method.upper():raise ValueError('Invalid browser method.')
            if version not in ('HTTP/1.0','HTTP/1.1'):raise ValueError('Unsupported browser request.')
            parsed,port=self.destination(method,target)
            header=None if method=='CONNECT' else self.request_headers(method,parsed,version,lines[1:])
            self.check(parsed.geturl())
            remote,upstream=await asyncio.wait_for(asyncio.open_connection(parsed.hostname,port),timeout=10)
            if method=='CONNECT':
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n');await writer.drain()
            else:
                upstream.write(header);await upstream.drain()
            check=lambda:self.check(parsed.geturl())
            pumps=[asyncio.create_task(self.pump(reader,upstream,check)),asyncio.create_task(self.pump(remote,writer,check))]
            done,_=await asyncio.wait(pumps,return_when=asyncio.FIRST_COMPLETED)
            for completed in done:completed.result()
            # After the server finishes its response, the browser can keep its end
            # open despite Connection: close. End this one-request tunnel promptly.
            await pumps[1]
        except RoutingBlocked:
            if upstream is None:
                try:await self.reply(writer,'403 Forbidden','Use the app connection for this service. Return to Desktop Buddy for details.')
                except (OSError,RuntimeError):pass
        except (ValueError,UnicodeError,asyncio.LimitOverrunError):
            try:await self.reply(writer,'400 Bad Request','Invalid browser request.')
            except (OSError,RuntimeError):pass
        except (OSError,asyncio.TimeoutError,asyncio.IncompleteReadError):
            if upstream is None:
                try:await self.reply(writer,'502 Bad Gateway','The website could not be reached.')
                except (OSError,RuntimeError):pass
        finally:
            for pump in pumps:pump.cancel()
            if pumps:await asyncio.gather(*pumps,return_exceptions=True)
            if upstream:upstream.close()
            writer.close();self.clients.discard(task)

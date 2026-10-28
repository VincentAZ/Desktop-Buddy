"""Host-native desktop control. Never changes compositor settings or input permissions."""
from __future__ import annotations
import hashlib
import io
import os
from pathlib import Path
import threading
import subprocess
import sys
import time
import uuid
from urllib.parse import unquote, urlsplit
from .security import Cancelled

class DesktopError(Exception):
    pass

class LazyBackend:
    """Unavailable desktop services must not prevent chat and file work."""
    def __init__(self, factory):
        self.factory = factory
        self.instance = None
        self.lock = threading.Lock()

    def __getattr__(self, name):
        with self.lock:
            if self.instance is None:self.instance = self.factory()
        return getattr(self.instance, name)

    def close(self):
        # Stopping an unused backend must not start a desktop connection.
        with self.lock:instance = self.instance
        if instance is not None and hasattr(instance, 'close'):instance.close()


class Portal:
    DEST='org.freedesktop.portal.Desktop'
    PATH='/org/freedesktop/portal/desktop'
    def __init__(self,parent_window='',restore_token='',save_token=None,load_token=None,*,capture_screen=True,progress=None):
        self.parent_window=parent_window
        self.capture_screen=capture_screen;self.progress=progress
        # GNOME can remember the screen-sharing grant; the token is stored by the app
        # and re-read before every connect, since GNOME issues a fresh one each time.
        self.restore_token=restore_token;self.save_token=save_token;self.load_token=load_token
        self.operation_lock=threading.Lock()
        import gi
        gi.require_version('Gio','2.0')
        from gi.repository import Gio,GLib
        self.Gio,self.GLib=Gio,GLib
        self.ready=threading.Event();self.session=None;self.pending_session=None;self.stream=None;self.stream_serial=None;self.size=None;self.screen_position=None;self.closed_subscription=None
        self.thread=threading.Thread(target=self._loop,daemon=True,name='desktop-portal')
        self.thread.start()
        if not self.ready.wait(5):raise DesktopError('Cannot connect to the desktop session bus.')
        if getattr(self,'error',None):raise DesktopError(self.error)

    def _loop(self):
        G=self.GLib;Gio=self.Gio
        self.context=G.MainContext.new();self.context.push_thread_default()
        try:
            self.bus=Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
            self.main_loop=G.MainLoop.new(self.context,False)
            self.ready.set();self.main_loop.run()
        except Exception as e:self.error=str(e);self.ready.set()
        finally:self.context.pop_thread_default()

    def on_loop(self,func):
        done=threading.Event();result=[]
        def run(*_):
            try:result.append((True,func()))
            except Exception as e:result.append((False,e))
            done.set();return False
        source=self.GLib.idle_source_new();source.set_callback(run);source.attach(self.context)
        if not done.wait(5):raise DesktopError('Desktop bus dispatcher did not respond.')
        if not result[0][0]:raise result[0][1]
        return result[0][1]

    def call(self,interface,method,signature,args,path=None):
        return self.bus.call_sync(self.DEST,path or self.PATH,'org.freedesktop.portal.'+interface,
            method,self.GLib.Variant(signature,args),None,self.Gio.DBusCallFlags.NONE,5000,None)

    def request(self,interface,method,signature,args,options,token,timeout=120):
        token.check();ident='buddy_'+uuid.uuid4().hex
        options={**options,'handle_token':self.GLib.Variant('s',ident)}
        sender=self.bus.get_unique_name().lstrip(':').replace('.','_')
        path=self.PATH+'/request/'+sender+'/'+ident
        done=threading.Event();responses={};response_lock=threading.Lock()
        self.last_request={'interface':interface,'method':method,'expected_path':path,'status':'waiting'}
        def callback(_bus,_sender,_path,_interface,_signal,parameters,*_):
            with response_lock:
                # Subscribe before calling so even an immediate legacy response is
                # retained. Only the returned request's result is ever accepted.
                if len(responses)<16 or _path==path:responses[_path]=parameters.unpack()
                if _path==path:done.set()
        subscription=self.on_loop(lambda:self.bus.signal_subscribe(self.DEST,'org.freedesktop.portal.Request','Response',None,None,self.Gio.DBusSignalFlags.NONE,callback,None))
        try:
            returned=self.call(interface,method,signature,(*args,options)).unpack()[0]
            if not isinstance(returned,str) or not returned.startswith(self.PATH+'/request/'):
                raise DesktopError('Your computer returned an invalid permission reply. Please try again.')
            with response_lock:
                path=returned
                self.last_request['returned_path']=path
                if path in responses:done.set()
                else:done.clear()
            until=time.monotonic()+timeout
            while not done.wait(.05):
                token.check()
                if time.monotonic()>until:
                    self.last_request['status']='timed_out'
                    raise DesktopError('Your computer did not finish connecting to Buddy. If you already clicked Share, the connection still did not complete. Try again from Settings → Computer and work folder → Let Buddy use my computer.')
            token.check()
            code,data=responses[path]
            self.last_request.update(status='responded',response_code=code)
            if code==1:raise DesktopError('Desktop permission was cancelled by the user.')
            if code!=0:raise DesktopError('Your computer could not connect to Buddy. Please try again.')
            return data
        finally:
            if not done.is_set():
                try:self.call('Request','Close','()',(),path)
                except Exception:pass
            self.bus.signal_unsubscribe(subscription)

    def check_unlocked(self):
        try:
            active=self.bus.call_sync('org.gnome.ScreenSaver','/org/gnome/ScreenSaver','org.gnome.ScreenSaver','GetActive',None,None,self.Gio.DBusCallFlags.NONE,2000,None).unpack()[0]
        except Exception:
            return
        if active:raise DesktopError('GNOME reports that the screen saver is active. Bring Buddy to the foreground and try Computer access again. This does not establish whether your session is locked.')

    def connect(self,token):
        self.check_unlocked()
        while not self.operation_lock.acquire(timeout=.05):token.check()
        try:
            token.check();return self._connect(token)
        finally:self.operation_lock.release()

    def _connect(self,token):
        if self.session:return {'screen_verified':self.stream is not None,'connected':True,'stream':self.stream,'logical_size':self.size,'screen_position':self.screen_position}
        if self.load_token:self.restore_token=self.load_token() or ''
        G=self.GLib
        result=self.request('RemoteDesktop','CreateSession','(a{sv})',(),{'session_handle_token':G.Variant('s','buddy_'+uuid.uuid4().hex)},token)
        session=result['session_handle'];self.pending_session=session
        try:
            self.closed_subscription=self.on_loop(lambda:self.bus.signal_subscribe(self.DEST,'org.freedesktop.portal.Session','Closed',session,None,self.Gio.DBusSignalFlags.NONE,self._closed,None))
            devices={'types':G.Variant('u',3),'persist_mode':G.Variant('u',2)}
            if self.restore_token:devices['restore_token']=G.Variant('s',self.restore_token)
            self.request('RemoteDesktop','SelectDevices','(oa{sv})',(session,),devices,token)
            capture_screen=getattr(self,'capture_screen',True)
            if capture_screen:
                self.request('ScreenCast','SelectSources','(oa{sv})',(session,),{'types':G.Variant('u',1),'multiple':G.Variant('b',False),'cursor_mode':G.Variant('u',2)},token)
            started=self.request('RemoteDesktop','Start','(osa{sv})',(session,self.parent_window),{},token)
            streams=started.get('streams',[])
            if capture_screen and not streams:raise DesktopError('No screen was shared. In the GNOME dialog, click the monitor so it is highlighted, then click Share.')
            if int(started.get('devices',0)) & 3 != 3:
                if self.restore_token:
                    # GNOME remembered a grant without remote control. Forget it so
                    # the dialog comes back and the person can switch control on.
                    self.restore_token=''
                    if self.save_token:self.save_token('')
                    self.close()
                    return self._connect(token)
                raise DesktopError('You clicked Share, but “Allow Remote Interaction” was switched off, so Buddy could see the screen but not click or type. Click Desktop control again and switch Allow Remote Interaction on before clicking Share.')
            stream=int(streams[0][0]) if streams else None
            props=streams[0][1] if streams else {}
            serial=props.get('pipewire-serial')
            remembered=started.get('restore_token','')
            if remembered and remembered!=self.restore_token:
                self.restore_token=remembered
                if self.save_token:self.save_token(remembered)
            if capture_screen:
                progress=getattr(self,'progress',None)
                if progress:progress('Checking that Buddy can see your shared screen…')
                self._validate_frame(self._stream_frame(session,stream,token,serial))
            token.check()
            if self.pending_session!=session:raise Cancelled('Desktop connection stopped.')
            self.stream=stream;self.stream_serial=serial
            self.size=props.get('logical_size') or props.get('size');self.screen_position=props.get('position')
            self.session=session;self.pending_session=None
            return {'screen_verified':capture_screen,'connected':True,'stream':self.stream,'logical_size':self.size,'screen_position':self.screen_position,'source':'GNOME consent portal','remembered':bool(remembered)}
        except Exception:
            self.close();raise

    def _closed(self,_bus,_sender,path,*_):
        if path not in (self.session,getattr(self,'pending_session',None)):return
        self.session=None;self.pending_session=None;self.stream=None;self.stream_serial=None;self.size=None;self.screen_position=None
        if self.closed_subscription:
            self.bus.signal_unsubscribe(self.closed_subscription);self.closed_subscription=None

    def close(self):
        if self.closed_subscription:
            self.bus.signal_unsubscribe(self.closed_subscription);self.closed_subscription=None
        sessions={self.session,getattr(self,'pending_session',None)}-{None}
        self.session=None;self.pending_session=None;self.stream=None;self.stream_serial=None;self.size=None;self.screen_position=None
        for session in sessions:
            try:self.call('Session','Close','()',(),session)
            except Exception:pass

    def screenshot(self,destination,token):
        self.check_unlocked()
        while not self.operation_lock.acquire(timeout=.05):token.check()
        try:
            token.check();return self._screenshot(destination,token)
        finally:self.operation_lock.release()

    @staticmethod
    def _validate_frame(data):
        if len(data)>40*1024*1024:raise DesktopError('Screenshot exceeded 40 MiB.')
        from PIL import Image
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        except Exception as error:
            raise DesktopError('Buddy received an unreadable screen image. Please reconnect computer access.') from error

    @staticmethod
    def _capture_frame_process(fd,stream,token,serial=None,timeout=12):
        command=[sys.executable,'-I',str(Path(__file__).with_name('capture_worker.py')),
                 '--fd',str(fd),'--stream',str(int(stream))]
        if serial is not None:command+=['--serial',str(int(serial))]
        token.check()
        process=subprocess.Popen(command,pass_fds=(fd,),stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        deadline=time.monotonic()+timeout
        try:
            while True:
                token.check()
                if time.monotonic()>deadline:
                    raise DesktopError('Buddy could not receive your shared screen. The screen check has stopped. Please reconnect computer access.')
                try:
                    data,error=process.communicate(timeout=.05)
                    break
                except subprocess.TimeoutExpired:pass
            token.check()
            if process.returncode:
                detail=error.decode('utf-8',errors='replace')[-2000:].strip()
                raise DesktopError('Screen capture failed: '+detail)
            Portal._validate_frame(data)
            return data
        finally:
            if process.poll() is None:process.kill()
            process.communicate()

    def _stream_frame(self,session,stream,token,serial=None):
        reply,fds=self.bus.call_with_unix_fd_list_sync(self.DEST,self.PATH,'org.freedesktop.portal.ScreenCast','OpenPipeWireRemote',
            self.GLib.Variant('(oa{sv})',(session,{})),None,self.Gio.DBusCallFlags.NONE,5000,None,None)
        fd=fds.get(reply.unpack()[0])
        try:return self._capture_frame_process(fd,stream,token,serial)
        finally:os.close(fd)

    def _screenshot(self,destination,token):
        if (not self.session or self.stream is None) and self.load_token and self.load_token():
            # GNOME remembered the screen-sharing grant: reconnect silently and
            # capture from the shared stream instead of asking again.
            try:self._connect(token)
            except Cancelled:raise
            except Exception:pass
        if not self.session or self.stream is None:
            result=self.request('Screenshot','Screenshot','(sa{sv})',(self.parent_window,),{'interactive':self.GLib.Variant('b',False)},token)
            uri=urlsplit(result['uri'])
            if uri.scheme!='file' or uri.netloc not in ('','localhost'):raise DesktopError('Portal returned a non-local screenshot.')
            data=Path(unquote(uri.path)).read_bytes()
        else:
            data=self._stream_frame(self.session,self.stream,token,getattr(self,'stream_serial',None))
        token.check()
        self._validate_frame(data)
        destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data);os.chmod(destination,0o600)
        from PIL import Image
        with Image.open(destination) as im:
            original=im.size
            im=im.convert('RGB');im.thumbnail((1920,1920));size=im.size;im.save(destination)
        data=destination.read_bytes()
        logical=list(self.size) if self.size else list(original)
        self.last_shot={'width':size[0],'height':size[1],'logical_size':logical,'screen_position':list(self.screen_position) if self.screen_position else [0,0]}
        return {'path':str(destination),'width':size[0],'height':size[1],'original_size':original,'logical_size':logical,'sha256':hashlib.sha256(data).hexdigest(),'screen_position':self.screen_position,
                'coordinate_note':f'This image is {size[0]}x{size[1]} pixels. To click or drag at a spot you see in it, pass desktop.input the pixel position in this image with coordinates="screenshot" (the default) and no target fields; Buddy converts it to the screen. desktop.accessibility bounds are screen coordinates: use coordinates="screen" for those.'}

    def from_screenshot(self,arguments):
        """Convert pixel positions in the latest screenshot into global logical coordinates."""
        shot=getattr(self,'last_shot',None)
        if not shot:raise DesktopError('Take a desktop.screenshot first; screenshot coordinates refer to that image.')
        result=dict(arguments)
        sx=shot['logical_size'][0]/shot['width'];sy=shot['logical_size'][1]/shot['height']
        for x,y in (('x','y'),('to_x','to_y')):
            if x not in result:continue
            if not 0<=result[x]<=shot['width'] or not 0<=result[y]<=shot['height']:
                raise DesktopError(f'That position is outside the screenshot ({shot["width"]}x{shot["height"]} pixels). Take a new screenshot and read the position from it.')
            # The last pixel row/column maps onto the monitor's last logical position.
            result[x]=shot['screen_position'][0]+min(result[x]*sx,shot['logical_size'][0]-1)
            result[y]=shot['screen_position'][1]+min(result[y]*sy,shot['logical_size'][1]-1)
        return result

    def notify(self,method,signature,args,token):
        token.check()
        if not self.session:raise DesktopError('Connect desktop control first and accept the GNOME dialog.')
        self.call('RemoteDesktop',method,'(oa{sv}'+signature+')',(self.session,{},*args))

    def move(self,x,y,token):
        if not self.stream:raise DesktopError('Computer access is not connected. Use desktop.connect first; the user chooses Share in the system dialog. The system may offer to remember permission.')
        if x<0 or y<0 or (self.size and (x>=self.size[0] or y>=self.size[1])):raise DesktopError('Coordinates are outside the shared monitor logical size.')
        self.notify('NotifyPointerMotionAbsolute','udd',(self.stream,float(x),float(y)),token)

    def screen_input(self,arguments):
        """Convert AT-SPI compositor coordinates to the shared stream's origin."""
        result=dict(arguments)
        if arguments['action'] not in ('move','click','drag','scroll'):return result
        if self.screen_position is None or self.size is None:
            raise DesktopError('The shared monitor position is unavailable. Use accessible actions; pointer coordinates cannot be verified.')
        for x,y in (('x','y'),('to_x','to_y')):
            if x not in result:continue
            result[x]-=self.screen_position[0];result[y]-=self.screen_position[1]
            if not 0<=result[x]<self.size[0] or not 0<=result[y]<self.size[1]:
                raise DesktopError('The target is outside the shared monitor. Share its monitor before using physical input.')
        return result

    def key(self,keys,token,verify_target=None):
        self.check_unlocked()
        import gi
        gi.require_version('Gdk','4.0')
        from gi.repository import Gdk
        aliases={'Ctrl':'Control_L','Control':'Control_L','Alt':'Alt_L','Shift':'Shift_L','Super':'Super_L','Enter':'Return','Esc':'Escape','Space':'space'}
        values=[Gdk.keyval_from_name(aliases.get(k,k)) for k in keys.split('+')]
        if not values or any(v in (0,0xffffff) for v in values):raise DesktopError('Unknown key. Example: Ctrl+Shift+t or Return.')
        pressed=[]
        try:
            for v in values:
                if verify_target:verify_target()
                self.notify('NotifyKeyboardKeysym','iu',(v,1),token);pressed.append(v)
        finally:
            for v in reversed(pressed):
                if self.session:
                    self.call('RemoteDesktop','NotifyKeyboardKeysym','(oa{sv}iu)',(self.session,{},v,0))

    def input(self,args,token,verify_target=None):
        self.check_unlocked()
        if verify_target:verify_target()
        action=args['action']
        if action=='move':self.move(args['x'],args['y'],token)
        elif action=='click':
            self.move(args['x'],args['y'],token)
            button={'left':272,'right':273,'middle':274}[args.get('button','left')]
            for _ in range(2 if args.get('double',False) else 1):
                if verify_target:verify_target()
                self.notify('NotifyPointerButton','iu',(button,1),token)
                try:token.wait(.04)
                finally:
                    if self.session:self.call('RemoteDesktop','NotifyPointerButton','(oa{sv}iu)',(self.session,{},button,0))
                token.wait(.08)
        elif action=='scroll':
            if 'x' in args and 'y' in args:self.move(args['x'],args['y'],token)
            if verify_target:verify_target()
            self.notify('NotifyPointerAxisDiscrete','ui',(int(args.get('axis',0)),int(args['steps'])),token)
        elif action=='key':self.key(args['keys'],token,verify_target)
        elif action=='type':
            import gi
            gi.require_version('Gdk','4.0')
            from gi.repository import Gdk
            text=args['text']
            if len(text)>4000:raise DesktopError('Type at most 4000 characters per action.')
            for ch in text:
                self.check_unlocked()
                if verify_target:verify_target()
                val={'\n':0xff0d,'\t':0xff09}.get(ch,Gdk.unicode_to_keyval(ord(ch)))
                self.notify('NotifyKeyboardKeysym','iu',(val,1),token)
                try:token.check()
                finally:
                    if self.session:self.call('RemoteDesktop','NotifyKeyboardKeysym','(oa{sv}iu)',(self.session,{},val,0))
        elif action=='drag':
            self.move(args['x'],args['y'],token)
            if verify_target:verify_target()
            self.notify('NotifyPointerButton','iu',(272,1),token)
            try:
                for i in range(1,21):
                    if verify_target:verify_target()
                    self.move(args['x']+(args['to_x']-args['x'])*i/20,args['y']+(args['to_y']-args['y'])*i/20,token);token.wait(.02)
            finally:
                if self.session:self.call('RemoteDesktop','NotifyPointerButton','(oa{sv}iu)',(self.session,{},272,0))
        else:raise DesktopError('Unknown desktop action.')
        return {'sent':True,'verified':False,'next':'Inspect accessibility or take a screenshot to verify the intended result.'}

class Accessibility:
    def __init__(self):
        import gi
        gi.require_version('Atspi','2.0')
        from gi.repository import Atspi
        self.A=Atspi
        Atspi.set_timeout(1000,1000)

    def node(self,path):
        if not path or not all(part.isdigit() for part in path.split('/')):
            raise DesktopError('Use the exact numeric control path returned by desktop.accessibility. Do not invent a path; inspect the app again.')
        obj=self.A.get_desktop(0)
        for part in path.split('/'):
            if part!='':obj=obj.get_child_at_index(int(part))
            if obj is None:raise DesktopError('Accessible element disappeared. Refresh the tree.')
        return obj

    def input_target(self,path,expected_name):
        return InputTarget(self,self.node(path),expected_name)

    def inspect(self,app='',depth=3,limit=300,window=''):
        root=self.A.get_desktop(0);result=[];until=time.monotonic()+8
        def walk(obj,path,level):
            if len(result)>=limit or time.monotonic()>until:return
            try:
                name=obj.get_name() or '';role=obj.get_role_name()
                if level==0 and app and app.lower() not in name.lower():return
                if level==1 and window and window.lower() not in name.lower():return
                item={'path':path,'name':name[:500],'role':role,'children':obj.get_child_count()}
                states=obj.get_state_set()
                item['focused']=states.contains(self.A.StateType.FOCUSED)
                if role!='password text':
                    try:
                        text=obj.get_text_iface()
                        if text:item['text']=self.A.Text.get_text(text,0,min(text.get_character_count(),4000))
                    except Exception:pass
                try:
                    component=obj.get_component_iface()
                    if component:
                        box=component.get_extents(self.A.CoordType.SCREEN)
                        item['bounds']={'x':box.x,'y':box.y,'width':box.width,'height':box.height}
                except Exception:pass
                try:
                    action_iface=obj.get_action_iface()
                    if action_iface:
                        item['actions']=[action_iface.get_action_name(index) for index in range(min(action_iface.get_n_actions(),32))]
                except Exception:pass
                result.append(item)
                if level<depth:
                    for i in range(min(obj.get_child_count(),100)):
                        walk(obj.get_child_at_index(i),path+'/'+str(i),level+1)
            except Exception:pass
        for i in range(root.get_child_count()):walk(root.get_child_at_index(i),str(i),0)
        response={'nodes':result,'truncated':len(result)>=limit or time.monotonic()>until,'source':'AT-SPI; apps may omit controls. Password text is excluded.'}
        if not result and (app or window):
            response['hint']='Application names differ from window titles. Retry with window only and omit app; omit both filters to list available apps and windows.'
            response['suggested_retry']={'window':window or app,'depth':max(depth,6),'limit':limit}
        elif depth<3:
            response['hint']='This depth shows apps and windows, not their nested controls. Increase depth to 6 or more to inspect editable fields.'
        return response

    def act(self,path,action,expected_name):
        obj=self.node(path)
        if obj.get_name()!=expected_name:raise DesktopError('Element changed since observation. Refresh before acting.')
        if action=='focus':
            ok=obj.get_component_iface().grab_focus()
            verified=bool(obj.get_state_set().contains(self.A.StateType.FOCUSED))
        else:
            iface=obj.get_action_iface()
            if not iface:raise DesktopError('Element has no accessible actions.')
            names=[iface.get_action_name(i) for i in range(iface.get_n_actions())]
            if action not in names:raise DesktopError('Available actions: '+', '.join(names))
            ok=iface.do_action(names.index(action));verified=False
        return {'accepted':bool(ok),'verified':verified,'next':'Reinspect the application to verify its resulting state.'}


class InputTarget:
    """Pin an AT-SPI object before approval and restore it before physical input.

    Tree indices can be reused while a dialog is open; keeping the original
    object prevents re-resolving the approved path to a different control.
    """
    def __init__(self,backend,obj,expected_name):
        self.backend=backend;self.obj=obj;self.name=expected_name
        self.role=obj.get_role_name()
        if obj.get_name()!=expected_name:raise DesktopError('The target changed. Inspect the app again before using it.')
        if obj.get_application().get_process_id()==os.getpid():
            raise DesktopError('Physical input cannot target Buddy’s own windows. Choose the application you want to use.')
        self.window=obj
        for _ in range(30):
            parent=self.window.get_parent()
            if parent is None or parent.get_role_name()=='application':break
            self.window=parent
        if self.window.get_role_name() not in ('frame','dialog','window'):
            raise DesktopError('Cannot identify the target window. Use an accessible control in the intended application.')
        self.window_name=self.window.get_name()
        self.original_bounds=self.bounds(self.window)
        self.was_active=self.window.get_state_set().contains(backend.A.StateType.ACTIVE)
        self.was_focused=self.was_active and self.obj.get_state_set().contains(backend.A.StateType.FOCUSED)
        self.check_identity()

    def bounds(self,obj):
        box=obj.get_component_iface().get_extents(self.backend.A.CoordType.SCREEN)
        return (box.x,box.y,box.width,box.height)

    def prepare_text(self):
        self.check_identity()
        if self.role=='password text':raise DesktopError('Enter passwords yourself in the application. Buddy does not read password fields.')
        self.editable=self.obj.get_editable_text_iface()
        self.text_iface=self.obj.get_text_iface()
        if not self.editable or not self.text_iface:raise DesktopError('This control does not support direct text entry. Inspect another accessible control or use verified physical input.')
        count=self.text_iface.get_character_count()
        if count>4000:raise DesktopError('Direct text entry is limited to fields containing at most 4000 characters.')
        self.before_text=self.backend.A.Text.get_text(self.text_iface,0,count)

    def set_text(self,text,token):
        self.check_identity();token.check()
        count=self.text_iface.get_character_count()
        if count>4000 or self.backend.A.Text.get_text(self.text_iface,0,count)!=self.before_text:
            raise DesktopError('The field changed during approval. Read it again before replacing its contents.')
        accepted=self.editable.set_text_contents(text)
        token.check()
        deadline=time.monotonic()+2
        while True:
            count=self.text_iface.get_character_count()
            verified=count==len(text) and self.backend.A.Text.get_text(self.text_iface,0,count)==text
            if verified or time.monotonic()>=deadline:break
            token.wait(.05)
        return {'accepted':bool(accepted),'verified':verified,'characters':count,
                'next':'Text readback matched. Observe any resulting app changes separately.' if verified else
                       'Text did not match after the request. Inspect the field before attempting another change.'}

    def check_identity(self,check_names=True):
        state=self.obj.get_state_set()
        if (state.contains(self.backend.A.StateType.DEFUNCT)
                or self.window.get_state_set().contains(self.backend.A.StateType.DEFUNCT)
                or self.obj.get_role_name()!=self.role
                or check_names and (self.obj.get_name()!=self.name or self.window.get_name()!=self.window_name)):
            raise DesktopError('The approved control or window changed. No further input was sent; inspect the app again.')

    def verify(self,require_control=True,check_geometry=False):
        self.check_identity(check_names=False)
        active=self.window.get_state_set().contains(self.backend.A.StateType.ACTIVE)
        focused=self.obj.get_state_set().contains(self.backend.A.StateType.FOCUSED)
        if not active or require_control and not focused:
            raise DesktopError('The intended app lost focus. No further input was sent; inspect it before continuing.')
        if check_geometry and self.bounds(self.window)!=self.original_bounds:
            raise DesktopError('The target window moved. No further input was sent; inspect its new position.')

    def prepare_input(self,arguments):
        if arguments.get('keyboard_scope','control')=='window':
            if arguments['action'] not in ('key','type'):
                raise DesktopError('Window keyboard scope is only for keys or typing.')
            if self.obj!=self.window:
                raise DesktopError('Window keyboard scope requires the observed window itself as the target.')
            if not self.was_active:
                raise DesktopError('The intended window must be active before requesting window keyboard input. Inspect the app again.')

    def restore(self,token,arguments,return_to_previous=None):
        self.check_identity();token.check()
        self.prepare_input(arguments)
        keyboard=arguments['action'] in ('type','key')
        control_keyboard=keyboard and arguments.get('keyboard_scope','control')=='control'
        can_return=self.was_focused if control_keyboard else self.was_active
        # Pointer targets and explicit window keyboard targets need an active
        # window, not a focusable AT-SPI child. Do not force grab_focus on them.
        restored=not control_keyboard and self.window.get_state_set().contains(self.backend.A.StateType.ACTIVE)
        if not restored:
            try:restored=self.obj.get_component_iface().grab_focus()
            except Exception:restored=False
        switched=False
        if not restored:
            if can_return and return_to_previous:
                # The target was foreground before Buddy's approval stole focus.
                # One compositor window switch may restore it; never cycle/hunt.
                return_to_previous()
                switched=True
            else:raise DesktopError('The intended control could not regain focus after approval. No input was sent. Use a direct accessible action when supported.')
        deadline=time.monotonic()+2
        while True:
            token.check()
            try:self.verify(control_keyboard);break
            except DesktopError:
                if can_return and return_to_previous and not switched:
                    return_to_previous();switched=True
                if time.monotonic()>=deadline:raise
                token.wait(.05)
        if self.bounds(self.window)!=self.original_bounds:
            raise DesktopError('The target window moved during approval. Inspect its new position before acting.')
        if arguments['action'] in ('move','click','scroll','drag'):
            x,y,width,height=self.bounds(self.obj)
            if not x<=arguments['x']<x+width or not y<=arguments['y']<y+height:
                raise DesktopError('The pointer position is outside the approved target. Inspect the control and its bounds again.')
            if arguments['action']=='drag':
                wx,wy,ww,wh=self.bounds(self.window)
                if not wx<=arguments['to_x']<wx+ww or not wy<=arguments['to_y']<wy+wh:
                    raise DesktopError('The drag destination is outside the approved window.')
        return lambda:self.verify(control_keyboard,check_geometry=not keyboard)

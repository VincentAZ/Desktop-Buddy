"""Host-native desktop control. Never changes compositor settings or input permissions."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import threading
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
    def __init__(self,parent_window='',restore_token='',save_token=None,load_token=None):
        self.parent_window=parent_window
        # GNOME can remember the screen-sharing grant; the token is stored by the app
        # and re-read before every connect, since GNOME issues a fresh one each time.
        self.restore_token=restore_token;self.save_token=save_token;self.load_token=load_token
        self.operation_lock=threading.Lock()
        import gi
        gi.require_version('Gio','2.0')
        from gi.repository import Gio,GLib
        self.Gio,self.GLib=Gio,GLib
        self.ready=threading.Event();self.session=None;self.stream=None;self.size=None;self.closed_subscription=None
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
        done=threading.Event();result=[]
        def callback(_bus,_sender,_path,_interface,_signal,parameters,*_):
            result.append(parameters.unpack());done.set()
        subscription=self.on_loop(lambda:self.bus.signal_subscribe(self.DEST,'org.freedesktop.portal.Request','Response',path,None,self.Gio.DBusSignalFlags.NONE,callback,None))
        try:
            self.call(interface,method,signature,(*args,options))
            until=time.monotonic()+timeout
            while not done.wait(.05):
                token.check()
                if time.monotonic()>until:raise DesktopError('GNOME did not finish its permission dialog within 2 minutes. If you never saw a dialog, GNOME itself is stuck: switch to another workspace and back (Ctrl+Alt+Down, then Ctrl+Alt+Up) and try again. If that does not help, log out and back in. If you did see it, it may be on another monitor.')
            code,data=result[0]
            if code==1:raise DesktopError('Desktop permission was cancelled by the user.')
            if code!=0:raise DesktopError('GNOME could not create this desktop session. Unlock the workstation, focus Desktop Buddy, and retry. No restrictions were changed.')
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
        if active:raise DesktopError('The GNOME session is locked. Unlock it normally and focus Desktop Buddy before granting desktop access.')

    def connect(self,token):
        self.check_unlocked()
        while not self.operation_lock.acquire(timeout=.05):token.check()
        try:return self._connect(token)
        finally:self.operation_lock.release()

    def _connect(self,token):
        if self.session:return {'connected':True,'stream':self.stream,'logical_size':self.size}
        if self.load_token:self.restore_token=self.load_token() or ''
        G=self.GLib
        result=self.request('RemoteDesktop','CreateSession','(a{sv})',(),{'session_handle_token':G.Variant('s','buddy_'+uuid.uuid4().hex)},token)
        session=result['session_handle'];self.session=session
        try:
            devices={'types':G.Variant('u',3),'persist_mode':G.Variant('u',2)}
            if self.restore_token:devices['restore_token']=G.Variant('s',self.restore_token)
            self.request('RemoteDesktop','SelectDevices','(oa{sv})',(session,),devices,token)
            self.request('ScreenCast','SelectSources','(oa{sv})',(session,),{'types':G.Variant('u',1),'multiple':G.Variant('b',False),'cursor_mode':G.Variant('u',2)},token)
            started=self.request('RemoteDesktop','Start','(osa{sv})',(session,self.parent_window),{},token)
            streams=started.get('streams',[])
            if not streams:raise DesktopError('No screen was shared. In the GNOME dialog, click the monitor so it is highlighted, then click Share.')
            if int(started.get('devices',0)) & 3 != 3:
                if self.restore_token:
                    # GNOME remembered a grant without remote control. Forget it so
                    # the dialog comes back and the person can switch control on.
                    self.restore_token=''
                    if self.save_token:self.save_token('')
                    self.close()
                    return self._connect(token)
                raise DesktopError('You clicked Share, but “Allow Remote Interaction” was switched off, so Buddy could see the screen but not click or type. Click Desktop control again and switch Allow Remote Interaction on before clicking Share.')
            self.stream=int(streams[0][0]);props=streams[0][1]
            remembered=started.get('restore_token','')
            if remembered and remembered!=self.restore_token:
                self.restore_token=remembered
                if self.save_token:self.save_token(remembered)
            self.size=props.get('logical_size') or props.get('size')
            token.check()
            if self.session!=session:raise Cancelled('Desktop connection stopped.')
            self.closed_subscription=self.on_loop(lambda:self.bus.signal_subscribe(self.DEST,'org.freedesktop.portal.Session','Closed',session,None,self.Gio.DBusSignalFlags.NONE,self._closed,None))
            return {'connected':True,'stream':self.stream,'logical_size':self.size,'source':'GNOME consent portal','remembered':bool(remembered)}
        except Exception:
            self.close();raise

    def _closed(self,_bus,_sender,path,*_):
        if path!=self.session:return
        self.session=None;self.stream=None;self.size=None
        if self.closed_subscription:
            self.bus.signal_unsubscribe(self.closed_subscription);self.closed_subscription=None

    def close(self):
        if self.closed_subscription:
            self.bus.signal_unsubscribe(self.closed_subscription);self.closed_subscription=None
        session=self.session
        self.session=None;self.stream=None;self.size=None
        if session:
            try:self.call('Session','Close','()',(),session)
            except Exception:pass

    def screenshot(self,destination,token):
        self.check_unlocked()
        while not self.operation_lock.acquire(timeout=.05):token.check()
        try:return self._screenshot(destination,token)
        finally:self.operation_lock.release()

    def _screenshot(self,destination,token):
        if not self.session:
            result=self.request('Screenshot','Screenshot','(sa{sv})',(self.parent_window,),{'interactive':self.GLib.Variant('b',False)},token)
            uri=urlsplit(result['uri'])
            if uri.scheme!='file' or uri.netloc not in ('','localhost'):raise DesktopError('Portal returned a non-local screenshot.')
            data=Path(unquote(uri.path)).read_bytes()
        else:
            import gi
            gi.require_version('Gst','1.0')
            from gi.repository import Gst
            Gst.init(None)
            reply,fds=self.bus.call_with_unix_fd_list_sync(self.DEST,self.PATH,'org.freedesktop.portal.ScreenCast','OpenPipeWireRemote',
                self.GLib.Variant('(oa{sv})',(self.session,{})),None,self.Gio.DBusCallFlags.NONE,5000,None,None)
            fd=fds.get(reply.unpack()[0]);pipeline=None;done=threading.Event();frames=[]
            try:
                pipeline=Gst.parse_launch(f'pipewiresrc fd={fd} path={self.stream} do-timestamp=true ! videoconvert ! video/x-raw,format=RGB ! pngenc ! appsink name=frames emit-signals=true max-buffers=1 drop=true sync=false')
                def frame(sink):
                    sample=sink.emit('pull-sample')
                    if sample and not frames:
                        buf=sample.get_buffer();frames.append(buf.extract_dup(0,buf.get_size()));done.set()
                    return Gst.FlowReturn.OK
                pipeline.get_by_name('frames').connect('new-sample',frame)
                pipeline.set_state(Gst.State.PLAYING)
                until=time.monotonic()+10
                while not done.wait(.05):
                    token.check()
                    msg=pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
                    if msg:raise DesktopError('PipeWire capture failed: '+str(msg.parse_error()[0]))
                    if time.monotonic()>until:raise DesktopError('No PipeWire frame arrived. Reconnect desktop sharing.')
                data=frames[0]
            finally:
                if pipeline:pipeline.set_state(Gst.State.NULL)
                os.close(fd)
        token.check()
        if len(data)>40*1024*1024:raise DesktopError('Screenshot exceeded 40 MiB.')
        destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data);os.chmod(destination,0o600)
        from PIL import Image
        with Image.open(destination) as im:
            original=im.size
            im=im.convert('RGB');im.thumbnail((1920,1920));size=im.size;im.save(destination)
        data=destination.read_bytes()
        return {'path':str(destination),'width':size[0],'height':size[1],'original_size':original,'logical_size':self.size or original,'sha256':hashlib.sha256(data).hexdigest(),'coordinate_note':'Scale image coordinates to logical_size before desktop.input.'}

    def notify(self,method,signature,args,token):
        token.check()
        if not self.session:raise DesktopError('Connect desktop control first and accept the GNOME dialog.')
        self.call('RemoteDesktop',method,'(oa{sv}'+signature+')',(self.session,{},*args))

    def move(self,x,y,token):
        if not self.stream:raise DesktopError('Desktop control is not connected. Run desktop.connect first; the user clicks Share in the GNOME dialog once and it is remembered.')
        if x<0 or y<0 or (self.size and (x>=self.size[0] or y>=self.size[1])):raise DesktopError('Coordinates are outside the shared monitor logical size.')
        self.notify('NotifyPointerMotionAbsolute','udd',(self.stream,float(x),float(y)),token)

    def key(self,keys,token):
        import gi
        gi.require_version('Gdk','4.0')
        from gi.repository import Gdk
        aliases={'Ctrl':'Control_L','Control':'Control_L','Alt':'Alt_L','Shift':'Shift_L','Super':'Super_L','Enter':'Return','Esc':'Escape','Space':'space'}
        values=[Gdk.keyval_from_name(aliases.get(k,k)) for k in keys.split('+')]
        if not values or any(v in (0,0xffffff) for v in values):raise DesktopError('Unknown key. Example: Ctrl+Shift+t or Return.')
        pressed=[]
        try:
            for v in values:
                self.notify('NotifyKeyboardKeysym','iu',(v,1),token);pressed.append(v)
        finally:
            for v in reversed(pressed):
                if self.session:
                    self.call('RemoteDesktop','NotifyKeyboardKeysym','(oa{sv}iu)',(self.session,{},v,0))

    def input(self,args,token):
        action=args['action']
        if action=='move':self.move(args['x'],args['y'],token)
        elif action=='click':
            self.move(args['x'],args['y'],token)
            button={'left':272,'right':273,'middle':274}[args.get('button','left')]
            for _ in range(2 if args.get('double',False) else 1):
                self.notify('NotifyPointerButton','iu',(button,1),token)
                try:token.wait(.04)
                finally:
                    if self.session:self.call('RemoteDesktop','NotifyPointerButton','(oa{sv}iu)',(self.session,{},button,0))
                token.wait(.08)
        elif action=='scroll':self.notify('NotifyPointerAxisDiscrete','ui',(int(args.get('axis',0)),int(args['steps'])),token)
        elif action=='key':self.key(args['keys'],token)
        elif action=='type':
            import gi
            gi.require_version('Gdk','4.0')
            from gi.repository import Gdk
            text=args['text']
            if len(text)>4000:raise DesktopError('Type at most 4000 characters per action.')
            for ch in text:
                val={'\n':0xff0d,'\t':0xff09}.get(ch,Gdk.unicode_to_keyval(ord(ch)))
                self.notify('NotifyKeyboardKeysym','iu',(val,1),token)
                try:token.check()
                finally:
                    if self.session:self.call('RemoteDesktop','NotifyKeyboardKeysym','(oa{sv}iu)',(self.session,{},val,0))
        elif action=='drag':
            self.move(args['x'],args['y'],token);self.notify('NotifyPointerButton','iu',(272,1),token)
            try:
                for i in range(1,21):
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
        obj=self.A.get_desktop(0)
        for part in path.split('/'):
            if part!='':obj=obj.get_child_at_index(int(part))
            if obj is None:raise DesktopError('Accessible element disappeared. Refresh the tree.')
        return obj

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
                        if text:item['text']=text.get_text(0,min(text.get_character_count(),4000))
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
        return {'nodes':result,'truncated':len(result)>=limit or time.monotonic()>until,'source':'AT-SPI; apps may omit controls. Password text is excluded.'}

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

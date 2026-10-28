"""One visible computer-permission request at a time, with plain outcomes."""
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk
from .ui_widgets import label,button


class ComputerAccessDialog:
    def __init__(self,app):
        self.app=app;self.token=None;self.pending=True;self.closed=False
        # Keep the system's permission window usable while it is transient for
        # Buddy's main window. This progress window must not be another modal gate.
        self.window=Gtk.Window(application=app,transient_for=app.window,
            title='Computer access',default_width=520,default_height=260)
        self.window.connect('close-request',self.close)
        body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
        for side in ('top','bottom','start','end'):getattr(body,'set_margin_'+side)(24)
        self.window.set_child(body)
        self.heading=label('Allow Buddy to use your computer','brand');body.append(self.heading)
        self.message=label('In your computer’s permission window, click the picture of your screen so it is selected, then click Share. Turn on “Allow Remote Interaction” if it appears.','muted',wrap=True)
        body.append(self.message)
        self.progress=Gtk.Spinner(spinning=True);body.append(self.progress)
        self.status=label('Waiting for your computer to confirm access…',wrap=True);body.append(self.status)
        self.done_button=button('Cancel',lambda *_:self.close());body.append(self.done_button)
        self.window.present()

    def finish(self,result=None,error=None,cancelled=False):
        if self.closed:return False
        self.pending=False;self.progress.stop();self.progress.set_visible(False)
        if cancelled:
            heading='Connection cancelled'
            message='This connection request has stopped.'
        elif error:
            heading='Buddy couldn’t connect'
            message='Your computer did not finish connecting to Buddy. If you already clicked Share, you did nothing wrong. The details have been saved so this can be investigated.'
        else:
            heading='Computer access is ready'
            message='Buddy can now use the shared screen, mouse and keyboard when your task allows it.'
        self.heading.set_text(heading);self.message.set_text(message)
        self.status.set_text('You can close this window.')
        self.done_button.set_label('Done');self.done_button.set_sensitive(True)
        return False

    def close(self,*_):
        if self.pending:
            if self.token:self.token.stop()
            self.status.set_text('Stopping this connection request…')
            self.done_button.set_sensitive(False)
            return True
        self.closed=True
        if getattr(self.app,'computer_access_dialog',None) is self:self.app.computer_access_dialog=None
        self.window.destroy();return True

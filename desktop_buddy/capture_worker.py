"""One screen frame in a disposable process. Input is an already-authorized FD."""
import argparse
import os
import sys
import threading
import time

MAX_FRAME_BYTES = 40 * 1024 * 1024


def capture(fd, stream, serial=None):
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    pipeline = Gst.parse_launch(
        f'pipewiresrc fd={fd} path={stream} name=screen do-timestamp=true '
        '! videoconvert ! video/x-raw,format=RGB ! pngenc '
        '! appsink name=frames emit-signals=true max-buffers=1 drop=true sync=false')
    finished = threading.Event()
    frames, errors = [], []

    def frame(sink):
        sample = sink.emit('pull-sample')
        if sample and not finished.is_set():
            buffer = sample.get_buffer()
            if buffer.get_size() > MAX_FRAME_BYTES:
                errors.append('Screenshot exceeded 40 MiB.')
            else:
                frames.append(buffer.extract_dup(0, buffer.get_size()))
            finished.set()
        return Gst.FlowReturn.OK

    try:
        if serial is not None:
            source = pipeline.get_by_name('screen')
            if not source.find_property('target-object'):
                raise RuntimeError('The PipeWire capture plugin needs an update.')
            source.set_property('path', None)
            source.set_property('target-object', str(serial))
        pipeline.get_by_name('frames').connect('new-sample', frame)
        pipeline.set_state(Gst.State.PLAYING)
        deadline = time.monotonic() + 10
        while not finished.wait(.05):
            message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if message:
                if message.type == Gst.MessageType.ERROR:
                    raise RuntimeError(str(message.parse_error()[0]))
                raise RuntimeError('Screen sharing ended before a frame arrived.')
            if time.monotonic() > deadline:
                raise TimeoutError('No screen frame arrived.')
        if errors: raise RuntimeError(errors[0])
        return frames[0]
    finally:
        # Some native backends also block here. The parent owns the hard deadline
        # and kills this process if startup, capture or cleanup does not finish.
        pipeline.set_state(Gst.State.NULL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fd', type=int, required=True)
    parser.add_argument('--stream', type=int, required=True)
    parser.add_argument('--serial', type=int)
    args = parser.parse_args()
    try:
        data = capture(args.fd, args.stream, args.serial)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        os.close(args.fd)


if __name__ == '__main__': sys.exit(main())

import io
import socket
import qrcode
import qrcode.image.svg
import gi
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk


def get_local_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


def make_qr_picture(url: str, size_px: int = 230) -> Gtk.Picture:
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(url, image_factory=factory, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)

    gbytes = GLib.Bytes.new(buf.getvalue())
    stream = Gio.MemoryInputStream.new_from_bytes(gbytes)
    pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, size_px, size_px, True, None)
    picture = Gtk.Picture.new_for_pixbuf(pixbuf)
    picture.set_size_request(size_px, size_px)
    picture.set_can_shrink(False)
    return picture
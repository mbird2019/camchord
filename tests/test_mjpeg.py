from camchord.mjpeg import Splitter


def jpeg(payload: bytes, extra_header: bytes = b"") -> bytes:
    """A minimal JPEG-shaped byte string: start, one table segment, a scan header, data, end."""
    table = b"\xff\xdb" + (2 + 4).to_bytes(2, "big") + b"\x00\x01\x02\x03"
    scan = b"\xff\xda" + (2 + 3).to_bytes(2, "big") + b"\x01\x00\x00"
    return b"\xff\xd8" + extra_header + table + scan + payload + b"\xff\xd9"


def test_whole_frames_in_one_chunk():
    a, b = jpeg(b"aaaa"), jpeg(b"bb\xff\x00bb")
    assert Splitter().feed(a + b) == [a, b]


def test_frames_arriving_byte_by_byte():
    a, b = jpeg(b"x" * 50), jpeg(b"y" * 7)
    splitter = Splitter()
    out = []
    for byte in a + b:
        out += splitter.feed(bytes([byte]))
    assert out == [a, b]


def test_end_marker_bytes_inside_a_header_segment_do_not_end_the_frame():
    app = b"\xff\xe1" + (2 + 4).to_bytes(2, "big") + b"\xff\xd9\xff\xd9"
    frame = jpeg(b"data", extra_header=app)
    assert Splitter().feed(frame) == [frame]


def test_junk_between_frames_is_skipped():
    a, b = jpeg(b"1"), jpeg(b"2")
    assert Splitter().feed(b"garbage" + a + b"\x00\x00" + b) == [a, b]


def test_restart_markers_in_picture_data():
    frame = jpeg(b"aa\xff\xd0bb\xff\xd7cc")
    assert Splitter().feed(frame) == [frame]


def test_incomplete_frame_is_held_back():
    frame = jpeg(b"abcdef")
    splitter = Splitter()
    assert splitter.feed(frame[:-3]) == []
    assert splitter.feed(frame[-3:]) == [frame]

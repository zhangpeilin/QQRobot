"""classifier 模块单元测试"""

from nonebot.adapters.onebot.v11 import Message

from plugins.media_archiver.classifier import classify_message, guess_extension


def _make_msg(segments: list[dict]) -> Message:
    """构造 OneBot v11 Message 对象"""
    msg = Message()
    for seg in segments:
        msg.append(seg)
    return msg


def test_classify_image():
    from nonebot.adapters.onebot.v11 import MessageSegment

    msg = Message([MessageSegment.image("https://example.com/test.jpg")])
    items = classify_message(msg)
    assert len(items) == 1
    assert items[0].media_type == "image"
    assert "example.com" in items[0].url


def test_classify_video():
    from nonebot.adapters.onebot.v11 import MessageSegment

    msg = Message([MessageSegment.video("https://example.com/test.mp4")])
    items = classify_message(msg)
    assert len(items) == 1
    assert items[0].media_type == "video"


def test_classify_mixed():
    from nonebot.adapters.onebot.v11 import MessageSegment

    msg = Message([
        MessageSegment.text("看看这张"),
        MessageSegment.image("https://example.com/a.jpg"),
        MessageSegment.text("还有这个视频"),
        MessageSegment.video("https://example.com/b.mp4"),
    ])
    items = classify_message(msg)
    assert len(items) == 2
    types = [i.media_type for i in items]
    assert "image" in types
    assert "video" in types


def test_classify_text_only():
    from nonebot.adapters.onebot.v11 import MessageSegment

    msg = Message([MessageSegment.text("纯文本消息")])
    items = classify_message(msg)
    assert len(items) == 0


def test_guess_extension_from_filename():
    assert guess_extension("", "photo.png") == ".png"
    assert guess_extension("", "video.mp4") == ".mp4"


def test_guess_extension_from_url():
    assert guess_extension("https://cdn.example.com/path/image.webp") == ".webp"


def test_guess_extension_default():
    assert guess_extension("", "", "image") == ".jpg"
    assert guess_extension("", "", "video") == ".mp4"
    assert guess_extension("", "", "record") == ".silk"

"""嵌套转发图片：appid=1406 CDN 失败后走 gchatpic MD5 回退"""

from plugins.media_archiver.listener import _gchatpic_url_from_filename


def test_gchatpic_url_from_md5_filename():
    url = _gchatpic_url_from_filename("0CEE4F6DB1646424A4274CEF11B3FEA2.jpg")
    assert url == (
        "https://gchat.qpic.cn/gchatpic_new/0/0-0-0CEE4F6DB1646424A4274CEF11B3FEA2/0"
    )


def test_gchatpic_url_lower_md5_is_uppercased():
    url = _gchatpic_url_from_filename("0cee4f6db1646424a4274cef11b3fea2.png")
    assert "0CEE4F6DB1646424A4274CEF11B3FEA2" in url


def test_gchatpic_url_rejects_non_md5_names():
    assert _gchatpic_url_from_filename("") == ""
    assert _gchatpic_url_from_filename("{733BC85E-3448-1885-3DA4-E6ED8562AE9E}.jpg") == ""
    assert _gchatpic_url_from_filename("not-a-hash.jpg") == ""
    assert _gchatpic_url_from_filename("deadbeef.jpg") == ""

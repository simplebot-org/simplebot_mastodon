class TestPlugin:
    """Offline tests"""

    def test_logout(self, mocker) -> None:
        msg = mocker.get_one_reply("/logout")
        assert "❌" in msg.text

    def test_bio(self, mocker) -> None:
        msg = mocker.get_one_reply("/bio")
        assert "❌" in msg.text

    def test_avatar(self, mocker) -> None:
        msg = mocker.get_one_reply("/avatar")
        assert "❌" in msg.text

    def test_dm(self, mocker) -> None:
        msg = mocker.get_one_reply("/dm")
        assert "❌" in msg.text

    def test_reply(self, mocker) -> None:
        msg = mocker.get_one_reply("/reply")
        assert "❌" in msg.text

    def test_star(self, mocker) -> None:
        msg = mocker.get_one_reply("/star")
        assert "❌" in msg.text

    def test_boost(self, mocker) -> None:
        msg = mocker.get_one_reply("/boost")
        assert "❌" in msg.text

    def test_open(self, mocker) -> None:
        msg = mocker.get_one_reply("/open")
        assert "❌" in msg.text

    def test_follow(self, mocker) -> None:
        msg = mocker.get_one_reply("/follow")
        assert "❌" in msg.text

    def test_unfollow(self, mocker) -> None:
        msg = mocker.get_one_reply("/unfollow")
        assert "❌" in msg.text

    def test_mute(self, mocker) -> None:
        msg = mocker.get_one_reply("/mute")
        assert "❌" in msg.text

    def test_unmute(self, mocker) -> None:
        msg = mocker.get_one_reply("/unmute")
        assert "❌" in msg.text

    def test_block(self, mocker) -> None:
        msg = mocker.get_one_reply("/block")
        assert "❌" in msg.text

    def test_unblock(self, mocker) -> None:
        msg = mocker.get_one_reply("/unblock")
        assert "❌" in msg.text

    def test_profile(self, mocker) -> None:
        msg = mocker.get_one_reply("/profile")
        assert "❌" in msg.text

    def test_local(self, mocker) -> None:
        msg = mocker.get_one_reply("/local")
        assert "❌" in msg.text

    def test_public(self, mocker) -> None:
        msg = mocker.get_one_reply("/public")
        assert "❌" in msg.text

    def test_tag(self, mocker) -> None:
        msg = mocker.get_one_reply("/tag")
        assert "❌" in msg.text

    def test_search(self, mocker) -> None:
        msg = mocker.get_one_reply("/search")
        assert "❌" in msg.text

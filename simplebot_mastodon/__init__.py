import os
import sqlite3
import time
from enum import Enum
from tempfile import NamedTemporaryFile
from threading import Thread
from typing import Any, Generator

import mastodon
import requests
import simplebot
from bs4 import BeautifulSoup
from deltachat import Chat, Contact, Message
from html2text import html2text
from pydub import AudioSegment
from pkg_resources import DistributionNotFound, get_distribution
from simplebot.bot import DeltaBot, Replies

from .db import DBManager


class Visibility(str, Enum):
    DIRECT = "direct"  # visible only to mentioned users
    PRIVATE = "private"  # visible only to followers
    UNLISTED = "unlisted"  # public but not appear on the public timeline
    PUBLIC = "public"  # post will be public


try:
    __version__ = get_distribution(__name__).version
except DistributionNotFound:
    # package is not installed
    __version__ = "0.0.0.dev0-unknown"
MASTODON_LOGO = os.path.join(os.path.dirname(__file__), "mastodon-logo.png")
v2emoji = {
    Visibility.DIRECT: "✉",
    Visibility.PRIVATE: "🔒",
    Visibility.UNLISTED: "🔓",
    Visibility.PUBLIC: "🌎",
}
TOOT_SEP = "\n\n―――――――――――――――\n\n"
STRFORMAT = "%Y-%m-%d %H:%M"
db: DBManager


@simplebot.hookimpl
def deltabot_init(bot: DeltaBot) -> None:
    global db
    db = _get_db(bot)

    _getdefault(bot, "delay", "30")
    _getdefault(bot, "max_users", "-1")
    _getdefault(bot, "max_users_instance", "-1")


@simplebot.hookimpl
def deltabot_start(bot: DeltaBot) -> None:
    Thread(target=_listen_to_mastodon, args=(bot,), daemon=True).start()


@simplebot.hookimpl
def deltabot_member_removed(
    bot: DeltaBot, chat: Chat, contact: Contact, replies: Replies
) -> None:
    me = bot.self_contact
    if me == contact or len(chat.get_contacts()) <= 1:
        acc = db.get_account(chat.id)
        if acc:
            if chat.id in (acc["home"], acc["notif"]):
                _logout(bot, acc, replies)
            else:
                db.remove_pchat(chat.id)


@simplebot.filter(name=__name__)
def filter_messages(message: Message, replies: Replies) -> None:
    """Process messages sent to a Mastodon chat."""
    acc = db.get_account_by_home(message.chat.id)
    if acc:
        _toot(_get_session(acc), message.text, message.filename)
        return

    pchat = db.get_pchat(message.chat.id)
    if pchat:
        acc = db.get_account_by_id(pchat["account"])
        text = "@{} {}".format(pchat["contact"], message.text)
        _toot(_get_session(acc), text, message.filename, visibility=Visibility.DIRECT)


@simplebot.command
def m_login(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Login on Mastodon. Example: /m_login mastodon.social me@example.com myPassw0rd"""
    api_url, email, passwd = payload.split(maxsplit=2)
    api_url = _normalize_url(api_url)

    max = int(_getdefault(bot, "max_users"))
    if max >= 0 and len(db.get_accounts()) >= max:
        replies.add(text="No more accounts allowed.")
        return
    max = int(_getdefault(bot, "max_users_instance"))
    if max >= 0 and len(db.get_accounts(url=api_url)) >= max:
        replies.add(text="No more accounts allowed from {}".format(api_url))
        return

    m = _get_session(dict(api_url=api_url, email=email, password=passwd))
    uname = m.me().acct.lower()

    old_user = db.get_account_by_user(uname, api_url)
    if old_user:
        replies.add(text="Account already in use")
        return

    n = m.notifications(limit=1)
    last_notif = n[0].id if n else None
    n = m.timeline_home(limit=1)
    last_home = n[0].id if n else None

    addr = message.get_sender_contact().addr
    url = _rmprefix(api_url, "https://")
    hgroup = bot.create_group("Home ({})".format(url), [addr])
    ngroup = bot.create_group("Notifications ({})".format(url), [addr])

    db.add_account(
        email, passwd, api_url, uname, addr, hgroup.id, ngroup.id, last_home, last_notif
    )

    hgroup.set_profile_image(MASTODON_LOGO)
    ngroup.set_profile_image(MASTODON_LOGO)
    text = "Messages sent here will be tooted to {}".format(api_url)
    replies.add(text=text, chat=hgroup)
    text = "Here you will receive notifications from {}".format(api_url)
    replies.add(text=text, chat=ngroup)


@simplebot.command
def m_logout(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Logout from Mastodon."""
    if payload:
        acc = db.get_account_by_id(int(payload))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]

    if acc:
        _logout(bot, acc, replies)
    else:
        replies.add(text="Unknow account")


@simplebot.command
def m_accounts(message: Message, replies: Replies) -> None:
    """Show your Mastodon accounts."""
    accs = db.get_accounts(addr=message.get_sender_contact().addr)
    if not accs:
        replies.add(text="Empty list")
        return
    text = ""
    for acc in accs:
        url = _rmprefix(acc["api_url"], "https://")
        text += "{}@{}: /m_logout_{}\n\n".format(acc["accname"], url, acc["id"])
    replies.add(text=text)


@simplebot.command
def m_bio(payload: str, message: Message, replies: Replies) -> None:
    """Update your Mastodon biography."""
    acc = db.get_account(message.chat.id)
    if not acc:
        accs = db.get_accounts(addr=message.get_sender_contact().addr)
        if len(accs) == 1:
            acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not payload:
        replies.add(text="You must provide a biography")
        return

    m = _get_session(acc)
    try:
        m.account_update_credentials(note=payload)
        replies.add(text="Biography updated")
    except mastodon.MastodonAPIError as err:
        replies.add(text=err.args[-1])


@simplebot.command
def m_avatar(message: Message, replies: Replies) -> None:
    """Update your Mastodon avatar."""
    acc = db.get_account(message.chat.id)
    if not acc:
        accs = db.get_accounts(addr=message.get_sender_contact().addr)
        if len(accs) == 1:
            acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not message.filename:
        replies.add(text="You must send an avatar attached to your messagee")
        return

    m = _get_session(acc)
    try:
        m.account_update_credentials(avatar=message.filename)
        replies.add(text="Avatar updated")
    except mastodon.MastodonAPIError:
        replies.add(text="Failed to update avatar")


@simplebot.command
def m_dm(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Start a private chat with the given Mastodon user."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    payload = payload.lstrip("@").lower()
    if not payload:
        replies.add(text="Wrong Syntax")
        return

    user = _get_user(_get_session(acc), payload)
    if not user:
        replies.add(text="Account not found: " + payload)
        return

    pv = db.get_pchat_by_contact(acc["id"], user.acct)
    if pv:
        chat = bot.get_chat(pv["id"])
        replies.add(text="Chat already exists, send messages here", chat=chat)
    else:
        title = "🇲 {} ({})".format(user.acct, _rmprefix(acc["api_url"], "https://"))
        g = bot.create_group(title, [acc["addr"]])
        db.add_pchat(g.id, payload, acc["id"])

        r = requests.get(user.avatar_static)
        with NamedTemporaryFile(
            dir=bot.account.get_blobdir(), suffix=".jpg", delete=False
        ) as file:
            path = file.name
        with open(path, "wb") as file:
            file.write(r.content)
        try:
            g.set_profile_image(path)
        except ValueError as err:
            bot.logger.exception(err)
        replies.add(text="Private chat with: " + user.acct, chat=g)


@simplebot.command
def m_reply(payload: str, message: Message, replies: Replies) -> None:
    """Reply to a toot with the given id."""
    acc_id, toot_id, text = payload.split(maxsplit=2)
    if not text and not message.filename:
        replies.add(text="Wrong Syntax")
        return

    addr = message.get_sender_contact().addr

    acc = db.get_account_by_id(int(acc_id))
    if not acc or acc["addr"] != addr:
        replies.add(text="Invalid toot or account id")
        return

    _toot(_get_session(acc), text=text, filename=message.filename, in_reply_to=toot_id)


@simplebot.command
def m_star(args: list, message: Message, replies: Replies) -> None:
    """Mark as favourite the toot with the given id."""
    acc_id, toot_id = args
    addr = message.get_sender_contact().addr

    acc = db.get_account_by_id(acc_id)
    if not acc or acc["addr"] != addr:
        replies.add(text="Invalid toot or account id")
        return

    m = _get_session(acc)
    m.status_favourite(toot_id)


@simplebot.command
def m_boost(args: list, message: Message, replies: Replies) -> None:
    """Boost the toot with the given id."""
    acc_id, toot_id = args
    addr = message.get_sender_contact().addr

    acc = db.get_account_by_id(acc_id)
    if not acc or acc["addr"] != addr:
        replies.add(text="Invalid toot or account id")
        return

    m = _get_session(acc)
    m.status_reblog(toot_id)


@simplebot.command
def m_cntx(args: list, message: Message, replies: Replies) -> None:
    """Get the context of the toot with the given id."""
    acc_id, toot_id = args
    addr = message.get_sender_contact().addr

    acc = db.get_account_by_id(acc_id)
    if not acc or acc["addr"] != addr:
        replies.add(text="Invalid toot or account id")
        return

    m = _get_session(acc)
    toots = m.status_context(toot_id)["ancestors"]
    if toots:
        replies.add(text=TOOT_SEP.join(_toots2text(toots[-3:], acc["id"])))
    else:
        replies.add(text="Nothing found")


@simplebot.command
def m_follow(payload: str, message: Message, replies: Replies) -> None:
    """Follow the user with the given id."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not payload:
        replies.add(text="Wrong Syntax")
        return

    m = _get_session(acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = _get_user(m, payload)
        if user_id is None:
            replies.add(text="Invalid user")
            return
    m.account_follow(user_id)
    replies.add(text="User followed")


@simplebot.command
def m_unfollow(payload: str, message: Message, replies: Replies) -> None:
    """Unfollow the user with the given id."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not payload:
        replies.add(text="Wrong Syntax")
        return

    m = _get_session(acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = _get_user(m, payload)
        if user_id is None:
            replies.add(text="Invalid user")
            return
    m.account_unfollow(user_id)
    replies.add(text="User unfollowed")


@simplebot.command
def m_mute(payload: str, message: Message, replies: Replies) -> None:
    """Mute the user with the given id."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not payload:
        replies.add(text="Wrong Syntax")
        return

    m = _get_session(acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = _get_user(m, payload)
        if user_id is None:
            replies.add(text="Invalid user")
            return
    m.account_mute(user_id)
    replies.add(text="User muted")


@simplebot.command
def m_unmute(payload: str, message: Message, replies: Replies) -> None:
    """Unmute the user with the given id."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not payload:
        replies.add(text="Wrong Syntax")
        return

    m = _get_session(acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = _get_user(m, payload)
        if user_id is None:
            replies.add(text="Invalid user")
            return
    m.account_unmute(user_id)
    replies.add(text="User unmuted")


@simplebot.command
def m_block(payload: str, message: Message, replies: Replies) -> None:
    """Block the user with the given id."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not payload:
        replies.add(text="Wrong Syntax")
        return

    m = _get_session(acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = _get_user(m, payload)
        if user_id is None:
            replies.add(text="Invalid user")
            return
    m.account_block(user_id)
    replies.add(text="User blocked")


@simplebot.command
def m_unblock(payload: str, message: Message, replies: Replies) -> None:
    """Unblock the user with the given id."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not payload:
        replies.add(text="Wrong Syntax")
        return

    m = _get_session(acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = _get_user(m, payload)
        if user_id is None:
            replies.add(text="Invalid user")
            return
    m.account_unblock(user_id)
    replies.add(text="User unblocked")


@simplebot.command
def m_profile(payload: str, message: Message, replies: Replies) -> None:
    """See the profile of the given user."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return

    m = _get_session(acc)
    me = m.me()
    if not payload:
        user = me
    else:
        user = _get_user(m, payload)
        if user is None:
            replies.add(text="Invalid user")
            return

    rel = m.account_relationships(user)[0] if user.id != me.id else None
    text = "{}:\n\n".format(_get_name(user))
    fields = ""
    for f in user.fields:
        fields += "{}: {}\n".format(
            html2text(f.name).strip(), html2text(f.value).strip()
        )
    if fields:
        text += fields + "\n\n"
    text += html2text(user.note).strip()
    text += "\n\nToots: {}\nFollowing: {}\nFollowers: {}".format(
        user.statuses_count, user.following_count, user.followers_count
    )
    if user.id != me.id:
        if rel["followed_by"]:
            text += "\n[follows you]"
        elif rel["blocked_by"]:
            text += "\n[blocked you]"
        text += "\n"
        if rel["following"] or rel["requested"]:
            action = "unfollow"
        else:
            action = "follow"
        text += "\n/m_{}_{}_{}".format(action, acc["id"], user.id)
        action = "unmute" if rel["muting"] else "mute"
        text += "\n/m_{}_{}_{}".format(action, acc["id"], user.id)
        action = "unblock" if rel["blocking"] else "block"
        text += "\n/m_{}_{}_{}".format(action, acc["id"], user.id)
        text += "\n/m_dm_{}_{}".format(acc["id"], user.id)
    text += TOOT_SEP
    toots = m.account_statuses(user, limit=10)
    text += TOOT_SEP.join(_toots2text(toots, acc["id"]))
    replies.add(text=text)


@simplebot.command
def m_local(payload: str, message: Message, replies: Replies) -> None:
    """Get latest entries from the local timeline."""
    if payload:
        acc = db.get_account_by_id(int(payload))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return

    m = _get_session(acc)
    toots = m.timeline_local()
    if toots:
        replies.add(text=TOOT_SEP.join(_toots2text(toots, acc["id"])))
    else:
        replies.add(text="Nothing found")


@simplebot.command
def m_public(payload: str, message: Message, replies: Replies) -> None:
    """Get latest entries from the public timeline."""
    if payload:
        acc = db.get_account_by_id(int(payload))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return

    m = _get_session(acc)
    toots = m.timeline_public()
    if toots:
        replies.add(text=TOOT_SEP.join(_toots2text(toots, acc["id"])))
    else:
        replies.add(text="Nothing found")


@simplebot.command
def m_tag(payload: str, message: Message, replies: Replies) -> None:
    """Get latest entries with the given hashtags."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    payload = payload.lstrip("#")
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not payload:
        replies.add(text="Wrong Syntax")
        return

    m = _get_session(acc)
    toots = m.timeline_hashtag(payload)
    if toots:
        replies.add(text=TOOT_SEP.join(_toots2text(toots, acc["id"])))
    else:
        replies.add(text="Nothing found")


@simplebot.command
def m_search(payload: str, message: Message, replies: Replies) -> None:
    """Search for users and hashtags matching the given text."""
    args = payload.split()
    if len(args) == 2:
        acc = db.get_account_by_id(int(args[0]))
        if acc and acc["addr"] != message.get_sender_contact().addr:
            replies.add(text="That is not your account")
            return
        payload = args[1]
    else:
        acc = db.get_account(message.chat.id)
        if not acc:
            accs = db.get_accounts(addr=message.get_sender_contact().addr)
            if len(accs) == 1:
                acc = accs[0]
    if not acc:
        replies.add(text="You must send that command in you Mastodon chats")
        return
    if not payload:
        replies.add(text="Wrong Syntax")
        return

    m = _get_session(acc)
    res = m.search(payload)
    text = ""
    if res["accounts"]:
        text += "👤 Accounts:"
        for a in res["accounts"]:
            text += "\n@{} /m_profile_{}_{}".format(a.acct, acc["id"], a.id)
        text += "\n\n"
    if res["hashtags"]:
        text += "#️⃣ Hashtags:"
        for tag in res["hashtags"]:
            text += "\n#{0} /m_tag_{1}_{0}".format(tag.name, acc["id"])
    if text:
        replies.add(text=text)
    else:
        replies.add(text="Nothing found")


def _get_session(acc) -> mastodon.Mastodon:
    client = db.get_client(acc["api_url"])
    if client:
        client_id, client_secret = client["id"], client["secret"]
    else:
        client_id, client_secret = mastodon.Mastodon.create_app(
            "DeltaChat Bridge", api_base_url=acc["api_url"]
        )
        db.add_client(acc["api_url"], client_id, client_secret)
    m = mastodon.Mastodon(
        client_id=client_id,
        client_secret=client_secret,
        api_base_url=acc["api_url"],
        ratelimit_method="throw",
    )
    m.log_in(acc["email"], acc["password"])
    return m


def _get_user(m, user_id) -> Any:
    user = None
    if user_id.isdigit():
        user = m.account(user_id)
    else:
        user_id = user_id.lstrip("@").lower()
        ids = (user_id, user_id.split("@")[0])
        for a in m.account_search(user_id):
            if a.acct.lower() in ids:
                user = a
                break
    return user


def _get_name(macc) -> str:
    isbot = "[BOT] " if macc.bot else ""
    if macc.display_name:
        return isbot + "{} (@{})".format(macc.display_name, macc.acct)
    return isbot + macc.acct


def _toots2text(toots: list, acc_id: int, notifications: bool = False) -> Generator:
    for t in reversed(toots):
        if notifications:
            is_mention = False
            timestamp = t.created_at.strftime(STRFORMAT)
            if t.type == "reblog":
                text = "🔁 {} boosted your toot. ({})\n\n".format(
                    _get_name(t.account), timestamp
                )
            elif t.type == "favourite":
                text = "⭐ {} favorited your toot. ({})\n\n".format(
                    _get_name(t.account), timestamp
                )
            elif t.type == "follow":
                yield "👤 {} followed you. ({})".format(_get_name(t.account), timestamp)
                continue
            elif t.type == "mention":
                is_mention = True
                text = "{}:\n\n".format(_get_name(t.account))
            else:
                continue
            t = t.status
        elif t.reblog:
            text = "{}:\n🔁 {}\n\n".format(
                _get_name(t.reblog.account), _get_name(t.account)
            )
            t = t.reblog
        else:
            text = "{}:\n\n".format(_get_name(t.account))

        media_urls = "\n".join(media.url for media in t.media_attachments)
        if media_urls:
            text += media_urls + "\n\n"

        soup = BeautifulSoup(t.content, "html.parser")
        if t.mentions:
            accts = {e.url: "@" + e.acct for e in t.mentions}
            for a in soup("a", class_="u-url"):
                name = accts.get(a["href"])
                if name:
                    a.string = name
        for br in soup("br"):
            br.replace_with("\n")
        for p in soup("p"):
            p.replace_with(p.get_text() + "\n\n")
        text += soup.get_text()

        text += "\n\n[{} {}]\n".format(
            v2emoji[t.visibility], t.created_at.strftime(STRFORMAT)
        )
        if not notifications or is_mention:
            text += "↩️ /m_reply_{}_{}\n".format(acc_id, t.id)
            text += "⭐ /m_star_{}_{}\n".format(acc_id, t.id)
            if t.visibility in (Visibility.PUBLIC, Visibility.UNLISTED):
                text += "🔁 /m_boost_{}_{}\n".format(acc_id, t.id)
            text += "⏫ /m_cntx_{}_{}\n".format(acc_id, t.id)

        yield text


def _toot(
    masto: mastodon.Mastodon,
    text: str = None,
    filename: str = None,
    visibility: str = None,
    in_reply_to: str = None,
) -> None:
    if filename:
        if filename.endswith(".aac"):
            aac_file = AudioSegment.from_file(filename, "aac")
            filename = filename[:-4] + ".mp3"
            aac_file.export(filename, format="mp3")
        media = [masto.media_post(filename).id]
        if in_reply_to:
            masto.status_reply(
                masto.status(in_reply_to), text, media_ids=media, visibility=visibility
            )
        else:
            masto.status_post(text, media_ids=media, visibility=visibility)
    elif text:
        if in_reply_to:
            masto.status_reply(masto.status(in_reply_to), text, visibility=visibility)
        else:
            masto.status_post(text, visibility=visibility)


def _normalize_url(url: str) -> str:
    if url.startswith("http://"):
        url = "https://" + url[4:]
    elif not url.startswith("https://"):
        url = "https://" + url
    return url.rstrip("/")


def _getdefault(bot: DeltaBot, key: str, value: str = None) -> str:
    val = bot.get(key, scope=__name__)
    if val is None and value is not None:
        bot.set(key, value, scope=__name__)
        val = value
    return val


def _get_db(bot: DeltaBot) -> DBManager:
    path = os.path.join(os.path.dirname(bot.account.db_path), __name__)
    if not os.path.exists(path):
        os.makedirs(path)
    return DBManager(os.path.join(path, "sqlite.db"))


def _rmprefix(text, prefix) -> str:
    return text[text.startswith(prefix) and len(prefix) :]


def _logout(bot: DeltaBot, acc, replies: Replies) -> None:
    me = bot.self_contact
    for pchat in db.get_pchats(acc["id"]):
        g = bot.get_chat(pchat["id"])
        try:
            g.remove_contact(me)
        except ValueError:
            pass
    try:
        bot.get_chat(acc["home"]).remove_contact(me)
    except ValueError:
        pass
    try:
        bot.get_chat(acc["notif"]).remove_contact(me)
    except ValueError:
        pass
    db.remove_account(acc["id"])
    replies.add(
        text="You have logged out from: " + acc["api_url"],
        chat=bot.get_chat(acc["addr"]),
    )


def _check_notifications(bot: DeltaBot, acc: sqlite3.Row, m: mastodon.Mastodon) -> None:
    max_id = None
    dmsgs = []
    notifications = []
    while True:
        ns = m.notifications(max_id=max_id, since_id=acc["last_notif"])
        if not ns:
            break
        if max_id is None:
            db.set_last_notif(acc["id"], ns[0].id)
        max_id = ns[-1]
        for n in ns:
            if (
                n.type == "mention"
                and n.status.visibility == Visibility.DIRECT
                and len(n.status.mentions) == 1
            ):
                dmsgs.append(n.status)
            else:
                notifications.append(n)
    for dm in reversed(dmsgs):
        text = "{}:\n\n".format(_get_name(dm.account))

        media_urls = "\n".join(media.url for media in dm.media_attachments)
        if media_urls:
            text += media_urls + "\n\n"

        soup = BeautifulSoup(dm.content, "html.parser")
        accts = {e.url: "@" + e.acct for e in dm.mentions}
        for a in soup("a", class_="u-url"):
            name = accts.get(a["href"])
            if name:
                a.string = name
        for br in soup("br"):
            br.replace_with("\n")
        for p in soup("p"):
            p.replace_with(p.get_text() + "\n\n")
        text += soup.get_text()
        text += "\n\n[{} {}]\n".format(
            v2emoji[dm.visibility], dm.created_at.strftime(STRFORMAT)
        )
        text += "⭐ /m_star_{}_{}\n".format(acc["id"], dm.id)

        pv = db.get_pchat_by_contact(acc["id"], dm.account.acct)
        if pv:
            g = bot.get_chat(pv["id"])
            if g is None:
                db.remove_pchat(pv["id"])
            else:
                g.send_text(text)
        else:
            url = _rmprefix(acc["api_url"], "https://")
            g = bot.create_group(
                "🇲 {} ({})".format(dm.account.acct, url), [acc["addr"]]
            )
            db.add_pchat(g.id, dm.account.acct, acc["id"])

            r = requests.get(dm.account.avatar_static)
            with NamedTemporaryFile(
                dir=bot.account.get_blobdir(), suffix=".jpg", delete=False
            ) as file:
                path = file.name
            with open(path, "wb") as file:
                file.write(r.content)
            try:
                g.set_profile_image(path)
            except ValueError as err:
                bot.logger.exception(err)

            g.send_text(text)

    bot.logger.debug(
        "Notifications: %s new entries (last id: %s)",
        len(notifications),
        acc["last_notif"],
    )
    if notifications:
        bot.get_chat(acc["notif"]).send_text(
            TOOT_SEP.join(_toots2text(notifications, acc["id"], True))
        )


def _check_home(bot: DeltaBot, acc: sqlite3.Row, m: mastodon.Mastodon) -> None:
    me = m.me()
    max_id = None
    toots: list = []
    while True:
        ts = m.timeline_home(max_id=max_id, since_id=acc["last_home"])
        if not ts:
            break
        if max_id is None:
            db.set_last_home(acc["id"], ts[0].id)
        max_id = ts[-1]
        for t in ts:
            for a in t.mentions:
                if a.id == me.id:
                    break
            else:
                toots.append(t)
    bot.logger.debug("Home: %s new entries (last id: %s)", len(toots), acc["last_home"])
    if toots:
        bot.get_chat(acc["home"]).send_text(
            TOOT_SEP.join(_toots2text(toots, acc["id"]))
        )


def _listen_to_mastodon(bot: DeltaBot) -> None:
    while True:
        bot.logger.info("Checking Mastodon")
        instances: dict = {}
        for acc in db.get_accounts():
            instances.setdefault(acc["api_url"], []).append(acc)
        while instances:
            for key in list(instances.keys()):
                if not instances[key]:
                    instances.pop(key)
                    continue
                acc = instances[key].pop()
                try:
                    m = _get_session(acc)
                    _check_notifications(bot, acc, m)
                    _check_home(bot, acc, m)
                except (mastodon.MastodonUnauthorizedError, mastodon.MastodonAPIError):
                    db.remove_account(acc["id"])
                    bot.get_chat(acc["addr"]).send_text(
                        "ERROR! You have been logged out from: " + acc["api_url"]
                    )
                except Exception as ex:
                    bot.logger.exception(ex)
            time.sleep(2)
        time.sleep(int(_getdefault(bot, "delay")))

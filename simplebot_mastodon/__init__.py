import os
from tempfile import NamedTemporaryFile
from threading import Thread

import mastodon
import requests
import simplebot
from deltachat import Chat, Contact, Message
from html2text import html2text
from pkg_resources import DistributionNotFound, get_distribution
from simplebot.bot import DeltaBot, Replies

from .db import DBManager
from .util import (
    TOOT_SEP,
    Visibility,
    get_db,
    get_name,
    get_session,
    get_user,
    getdefault,
    listen_to_mastodon,
    logout,
    normalize_url,
    rmprefix,
    send_toot,
    toots2text,
)

try:
    __version__ = get_distribution(__name__).version
except DistributionNotFound:
    # package is not installed
    __version__ = "0.0.0.dev0-unknown"
MASTODON_LOGO = os.path.join(os.path.dirname(__file__), "mastodon-logo.png")
db: DBManager


@simplebot.hookimpl
def deltabot_init(bot: DeltaBot) -> None:
    global db
    db = get_db(bot)

    getdefault(bot, "delay", "30")
    getdefault(bot, "max_users", "-1")
    getdefault(bot, "max_users_instance", "-1")


@simplebot.hookimpl
def deltabot_start(bot: DeltaBot) -> None:
    Thread(target=listen_to_mastodon, args=(bot,), daemon=True).start()


@simplebot.hookimpl
def deltabot_member_removed(
    bot: DeltaBot, chat: Chat, contact: Contact, replies: Replies
) -> None:
    me = bot.self_contact
    if me == contact or len(chat.get_contacts()) <= 1:
        acc = db.get_account(chat.id)
        if acc:
            if chat.id in (acc["home"], acc["notif"]):
                logout(db, bot, acc, replies)
            else:
                db.remove_pchat(chat.id)


@simplebot.filter(name=__name__)
def filter_messages(message: Message) -> None:
    """Process messages sent to a Mastodon chat."""
    acc = db.get_account_by_home(message.chat.id)
    if acc:
        send_toot(get_session(db, acc), message.text, message.filename)
        return

    pchat = db.get_pchat(message.chat.id)
    if pchat:
        acc = db.get_account_by_id(pchat["account"])
        text = f"@{pchat['contact']} {message.text}"
        send_toot(
            get_session(db, acc), text, message.filename, visibility=Visibility.DIRECT
        )


@simplebot.command
def m_login(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Login on Mastodon. Example: /m_login mastodon.social me@example.com myPassw0rd"""
    api_url, email, passwd = payload.split(maxsplit=2)
    api_url = normalize_url(api_url)

    maximum = int(getdefault(bot, "max_users"))
    if 0 <= maximum <= len(db.get_accounts()):
        replies.add(text="No more accounts allowed.")
        return
    maximum = int(getdefault(bot, "max_users_instance"))
    if 0 <= maximum <= len(db.get_accounts(url=api_url)):
        replies.add(text=f"No more accounts allowed from {api_url}")
        return

    m = get_session(db, dict(api_url=api_url, email=email, password=passwd))
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
    url = rmprefix(api_url, "https://")
    hgroup = bot.create_group(f"Home ({url})", [addr])
    ngroup = bot.create_group(f"Notifications ({url})", [addr])

    db.add_account(
        email, passwd, api_url, uname, addr, hgroup.id, ngroup.id, last_home, last_notif
    )

    hgroup.set_profile_image(MASTODON_LOGO)
    ngroup.set_profile_image(MASTODON_LOGO)
    text = f"Messages sent here will be tooted to {api_url}"
    replies.add(text=text, chat=hgroup)
    text = f"Here you will receive notifications from {api_url}"
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
        logout(db, bot, acc, replies)
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
        url = rmprefix(acc["api_url"], "https://")
        text += f"{acc['accname']}@{url}: /m_logout_{acc['id']}\n\n"
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

    m = get_session(db, acc)
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

    m = get_session(db, acc)
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

    user = get_user(get_session(db, acc), payload)
    if not user:
        replies.add(text="Account not found: " + payload)
        return

    pv = db.get_pchat_by_contact(acc["id"], user.acct)
    if pv:
        chat = bot.get_chat(pv["id"])
        replies.add(text="Chat already exists, send messages here", chat=chat)
    else:
        title = f"🇲 {user.acct} ({rmprefix(acc['api_url'], 'https://')})"
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

    send_toot(
        get_session(db, acc), text=text, filename=message.filename, in_reply_to=toot_id
    )


@simplebot.command
def m_star(args: list, message: Message, replies: Replies) -> None:
    """Mark as favourite the toot with the given id."""
    acc_id, toot_id = args
    addr = message.get_sender_contact().addr

    acc = db.get_account_by_id(acc_id)
    if not acc or acc["addr"] != addr:
        replies.add(text="Invalid toot or account id")
        return

    m = get_session(db, acc)
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

    m = get_session(db, acc)
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

    m = get_session(db, acc)
    toots = m.status_context(toot_id)["ancestors"]
    if toots:
        replies.add(text=TOOT_SEP.join(toots2text(toots[-3:], acc["id"])))
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

    m = get_session(db, acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = get_user(m, payload)
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

    m = get_session(db, acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = get_user(m, payload)
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

    m = get_session(db, acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = get_user(m, payload)
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

    m = get_session(db, acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = get_user(m, payload)
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

    m = get_session(db, acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = get_user(m, payload)
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

    m = get_session(db, acc)
    if payload.isdigit():
        user_id = payload
    else:
        user_id = get_user(m, payload)
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

    m = get_session(db, acc)
    me = m.me()
    if not payload:
        user = me
    else:
        user = get_user(m, payload)
        if user is None:
            replies.add(text="Invalid user")
            return

    rel = m.account_relationships(user)[0] if user.id != me.id else None
    text = f"{get_name(user)}:\n\n"
    fields = ""
    for f in user.fields:
        fields += f"{html2text(f.name).strip()}: {html2text(f.value).strip()}\n"
    if fields:
        text += fields + "\n\n"
    text += html2text(user.note).strip()
    text += f"\n\nToots: {user.statuses_count}\nFollowing: {user.following_count}\nFollowers: {user.followers_count}"
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
        text += f"\n/m_{action}_{acc['id']}_{user.id}"
        action = "unmute" if rel["muting"] else "mute"
        text += f"\n/m_{action}_{acc['id']}_{user.id}"
        action = "unblock" if rel["blocking"] else "block"
        text += f"\n/m_{action}_{acc['id']}_{user.id}"
        text += f"\n/m_dm_{acc['id']}_{user.id}"
    text += TOOT_SEP
    toots = m.account_statuses(user, limit=10)
    text += TOOT_SEP.join(toots2text(toots, acc["id"]))
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

    m = get_session(db, acc)
    toots = m.timeline_local()
    if toots:
        replies.add(text=TOOT_SEP.join(toots2text(toots, acc["id"])))
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

    m = get_session(db, acc)
    toots = m.timeline_public()
    if toots:
        replies.add(text=TOOT_SEP.join(toots2text(toots, acc["id"])))
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

    m = get_session(db, acc)
    toots = m.timeline_hashtag(payload)
    if toots:
        replies.add(text=TOOT_SEP.join(toots2text(toots, acc["id"])))
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

    m = get_session(db, acc)
    res = m.search(payload)
    text = ""
    if res["accounts"]:
        text += "👤 Accounts:"
        for a in res["accounts"]:
            text += f"\n@{a.acct} /m_profile_{acc['id']}_{a.id}"
        text += "\n\n"
    if res["hashtags"]:
        text += "#️⃣ Hashtags:"
        for tag in res["hashtags"]:
            text += f"\n#{tag.name} /m_tag_{acc['id']}_{tag.name}"
    if text:
        replies.add(text=text)
    else:
        replies.add(text="Nothing found")

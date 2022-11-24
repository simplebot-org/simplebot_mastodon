"""hooks, filters and commands"""

import os
from threading import Thread
from typing import List

import mastodon
import simplebot
from deltachat import Chat, Contact, Message
from simplebot.bot import DeltaBot, Replies

from .migrations import run_migrations
from .orm import Account, DmChat, OAuth, init, session_scope
from .util import (
    TOOT_SEP,
    Visibility,
    account_action,
    download_file,
    get_account_from_msg,
    get_client,
    get_database_path,
    get_mastodon,
    get_mastodon_from_msg,
    get_profile,
    get_user,
    getdefault,
    listen_to_mastodon,
    normalize_url,
    send_toot,
    toots2texts,
)

MASTODON_LOGO = os.path.join(os.path.dirname(__file__), "mastodon-logo.png")


@simplebot.hookimpl
def deltabot_init(bot: DeltaBot) -> None:
    getdefault(bot, "delay", "30")
    getdefault(bot, "max_users", "-1")
    getdefault(bot, "max_users_instance", "-1")
    pref = getdefault(bot, "cmd_prefix", "")

    bot.commands.register(func=logout_cmd, name=f"/{pref}logout")
    bot.commands.register(func=reply_cmd, name=f"/{pref}reply", hidden=True)
    bot.commands.register(func=star_cmd, name=f"/{pref}star", hidden=True)
    bot.commands.register(func=boost_cmd, name=f"/{pref}boost", hidden=True)
    bot.commands.register(func=open_cmd, name=f"/{pref}open", hidden=True)
    bot.commands.register(func=avatar_cmd, name=f"/{pref}avatar")
    bot.commands.register(func=local_cmd, name=f"/{pref}local")
    bot.commands.register(func=public_cmd, name=f"/{pref}public")

    desc = f"Login on Mastodon.\n\nExample:\n/{pref}login mastodon.social\n\nTo login without OAuth:\n/{pref}login mastodon.social me@example.com myPassw0rd"
    bot.commands.register(func=login_cmd, name=f"/{pref}login", help=desc)

    desc = f"Update your Mastodon biography.\n\nExample:\n/{pref}bio I love Delta Chat"
    bot.commands.register(func=bio_cmd, name=f"/{pref}bio", help=desc)

    desc = f"Start a private chat with the given Mastodon user.\n\nExample:\n/{pref}dm user@mastodon.social"
    bot.commands.register(func=dm_cmd, name=f"/{pref}dm", help=desc)

    desc = f"Follow the user with the given account name or id.\n\nExample:\n/{pref}follow user@mastodon.social"
    bot.commands.register(func=follow_cmd, name=f"/{pref}follow", help=desc)

    desc = f"Unfollow the user with the given account name or id.\n\nExample:\n/{pref}unfollow user@mastodon.social"
    bot.commands.register(func=unfollow_cmd, name=f"/{pref}unfollow", help=desc)

    desc = f"Mute the user with the given account name or id. If sent in the Home chat it will mute the Home timeline.\n\nExample:\n/{pref}mute user@mastodon.social\n\nTo mute Home timeline:\n/{pref}mute"
    bot.commands.register(func=mute_cmd, name=f"/{pref}mute", help=desc)

    desc = f"Unmute the user with the given account name or id. If sent in the Home chat it will unmute the Home timeline.\n\nExample:\n/{pref}unmute user@mastodon.social\n\nTo unmute Home timeline:\n/{pref}unmute"
    bot.commands.register(func=unmute_cmd, name=f"/{pref}unmute", help=desc)

    desc = f"Block the user with the given account name or id.\n\nExample:\n/{pref}block user@mastodon.social"
    bot.commands.register(func=block_cmd, name=f"/{pref}block", help=desc)

    desc = f"Unblock the user with the given account name or id.\n\nExample:\n/{pref}unblock user@mastodon.social"
    bot.commands.register(func=unblock_cmd, name=f"/{pref}unblock", help=desc)

    desc = f"See the profile of the given user.\n\nExample:\n/{pref}profile user@mastodon.social"
    bot.commands.register(func=profile_cmd, name=f"/{pref}profile", help=desc)

    desc = f"Get latest entries with the given hashtags.\n\nExamples:\n/{pref}tag deltachat\n/{pref}tag mastocat"
    bot.commands.register(func=tag_cmd, name=f"/{pref}tag", help=desc)

    desc = f"Search for users and hashtags matching the given text.\n\nExamples:\n/{pref}search deltachat\n/{pref}search mastocat"
    bot.commands.register(func=search_cmd, name=f"/{pref}search", help=desc)


@simplebot.hookimpl
def deltabot_start(bot: DeltaBot) -> None:
    run_migrations(bot)
    init(f"sqlite:///{get_database_path(bot)}")
    Thread(target=listen_to_mastodon, args=(bot,), daemon=True).start()


@simplebot.hookimpl
def deltabot_member_removed(
    bot: DeltaBot, chat: Chat, contact: Contact, replies: Replies
) -> None:
    if bot.self_contact != contact and len(chat.get_contacts()) > 1:
        return

    url = ""
    chats: List[int] = []
    with session_scope() as session:
        acc = (
            session.query(Account)
            .filter((Account.home == chat.id) | (Account.notifications == chat.id))
            .first()
        )
        if acc:
            url = acc.url
            addr = acc.addr
            chats.extend(dmchat.chat_id for dmchat in acc.dm_chats)
            chats.append(acc.home)
            chats.append(acc.notifications)
            session.delete(acc)
        else:
            dmchat = session.query(DmChat).filter_by(chat_id=chat.id).first()
            if dmchat:
                chats.append(chat.id)
                session.delete(dmchat)

    for chat_id in chats:
        try:
            bot.get_chat(chat_id).remove_contact(bot.self_contact)
        except ValueError:
            pass

    if url:
        replies.add(text=f"✔️ You logged out from: {url}", chat=bot.get_chat(addr))


@simplebot.filter
def filter_messages(bot: DeltaBot, message: Message, replies: Replies) -> None:
    """Once you log in with your Mastodon credentials, two chats will be created for you:

    • The Home chat is where you will receive your Home timeline and any message you send in that chat will be published on Mastodon.
    • The Notifications chat is where you will receive your Mastodon notifications.

    When a Mastodon user writes a private/direct message to you, a chat will be created for your private conversation with that user.
    """
    if not message.chat.is_multiuser():
        addr = message.get_sender_contact().addr
        with session_scope() as session:
            auth = session.query(OAuth).filter_by(addr=addr).first()
            if not auth:
                replies.add(
                    text="❌ To publish messages you must send them in your Home chat.",
                    quote=message,
                )
                return
            url, user, client_id, client_secret = (
                auth.url,
                auth.user,
                auth.client_id,
                auth.client_secret,
            )
        m = get_mastodon(url, client_id=client_id, client_secret=client_secret)
        try:
            m.log_in(code=message.text.strip())
            _login(addr, user, m, bot, replies)
            with session_scope() as session:
                session.delete(session.query(OAuth).filter_by(addr=addr).first())
        except Exception:  # noqa
            text = "❌ Authentication failed, generate another authorization code and send it here"
            replies.add(text=text, quote=message)
        return

    api_url: str = ""
    with session_scope() as session:
        acc = (
            session.query(Account)
            .filter(
                (Account.home == message.chat.id)
                | (Account.notifications == message.chat.id)
            )
            .first()
        )
        if acc:
            if acc.home == message.chat.id:
                api_url = acc.url
                token = acc.token
                args: tuple = (message.text, message.filename)
        else:
            dmchat = session.query(DmChat).filter_by(chat_id=message.chat.id).first()
            if dmchat:
                api_url = dmchat.account.url
                token = dmchat.account.token
                args = (
                    f"@{dmchat.contact} {message.text}",
                    message.filename,
                    Visibility.DIRECT,
                )

    if api_url:
        send_toot(get_mastodon(api_url, token), *args)


def login_cmd(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    args = payload.split(maxsplit=2)
    if len(args) == 1:
        api_url, email, passwd = args[0], None, None
    else:
        if len(args) != 3:
            replies.add(text="❌ Wrong usage", quote=message)
            return
        api_url, email, passwd = args
    api_url = normalize_url(api_url)
    addr = message.get_sender_contact().addr

    user = ""
    with session_scope() as session:
        acc = session.query(Account).filter_by(addr=addr).first()
        if acc:
            if acc.url != api_url:
                replies.add(text="❌ You are already logged in.")
                return
            user = acc.user
        else:
            maximum = int(getdefault(bot, "max_users"))
            if 0 <= maximum <= session.query(Account).count():
                replies.add(text="❌ No more users allowed in this bot.")
                return
            maximum = int(getdefault(bot, "max_users_instance"))
            if 0 <= maximum <= session.query(Account).filter_by(url=api_url).count():
                replies.add(text=f"❌ No more users from {api_url} allowed in this bot")
                return

        client_id, client_secret = get_client(session, api_url)

    m = get_mastodon(api_url, client_id=client_id, client_secret=client_secret)

    if email:
        m.log_in(email, passwd)
        _login(addr, user, m, bot, replies)
    else:
        if client_id is None:
            replies.add(
                text="❌ Server doesn't seem to support OAuth.",
                quote=message,
            )
            return
        with session_scope() as session:
            auth = session.query(OAuth).filter_by(addr=addr).first()
            if not auth:
                session.add(
                    OAuth(
                        addr=addr,
                        url=api_url,
                        user=user,
                        client_id=client_id,
                        client_secret=client_secret,
                    )
                )
            else:
                auth.url = api_url
                auth.client_id = client_id
                auth.client_secret = client_secret
                auth.user = user
        auth_url = m.auth_request_url()
        text = (
            f"To grant access to your account, open this URL:\n\n{auth_url}\n\n"
            "You will get an authorization code, copy it and send it here"
        )
        replies.add(text=text, quote=message)


def _login(
    addr: str, user: str, masto: mastodon.Mastodon, bot: DeltaBot, replies: Replies
) -> None:
    uname = masto.me().acct.lower()

    if user:
        if user == uname:
            with session_scope() as session:
                acc = session.query(Account).filter_by(addr=addr).first()
                acc.token = masto.access_token
                replies.add(text="✔️ You refreshed your credentials.")
        else:
            replies.add(text="❌ You are already logged in.")
        return

    n = masto.notifications(limit=1)
    last_notif = n[0].id if n else None
    n = masto.timeline_home(limit=1)
    last_home = n[0].id if n else None

    api_url = masto.api_base_url
    url = api_url.split("://", maxsplit=1)[-1]
    hgroup = bot.create_group(f"Home ({url})", [addr])
    ngroup = bot.create_group(f"Notifications ({url})", [addr])

    with session_scope() as session:
        session.add(
            Account(
                addr=addr,
                user=uname,
                url=api_url,
                token=masto.access_token,
                home=hgroup.id,
                notifications=ngroup.id,
                last_home=last_home,
                last_notif=last_notif,
            )
        )

    hgroup.set_profile_image(MASTODON_LOGO)
    pref = getdefault(bot, "cmd_prefix", "")
    replies.add(
        text=f"ℹ️ Messages sent here will be published in @{uname}@{url}\n\nIf your Home timeline is too noisy and you would like to disable incoming toots, send /{prefix}mute here.",
        chat=hgroup,
    )

    ngroup.set_profile_image(MASTODON_LOGO)
    replies.add(
        text=f"ℹ️ Here you will receive notifications for @{uname}@{url}", chat=ngroup
    )


def logout_cmd(bot: DeltaBot, message: Message, replies: Replies) -> None:
    """Logout from Mastodon."""
    addr = message.get_sender_contact().addr
    chats: List[int] = []
    with session_scope() as session:
        acc = session.query(Account).filter_by(addr=addr).first()
        if acc:
            text = f"✔️ You logged out from: {acc.url}"
            chats.extend(dmchat.chat_id for dmchat in acc.dm_chats)
            chats.append(acc.home)
            chats.append(acc.notifications)
            session.delete(acc)
        else:
            text = "❌ You are not logged in"

    for chat_id in chats:
        try:
            bot.get_chat(chat_id).remove_contact(bot.self_contact)
        except ValueError:
            pass
    replies.add(text=text, chat=bot.get_chat(addr))


def bio_cmd(payload: str, message: Message, replies: Replies) -> None:
    if not payload:
        replies.add(text="❌ Wrong usage", quote=message)
        return

    masto = get_mastodon_from_msg(message)
    if masto:
        try:
            masto.account_update_credentials(note=payload)
            text = "✔️ Biography updated"
        except mastodon.MastodonAPIError as err:
            text = f"❌ ERROR: {err.args[-1]}"
    else:
        text = "❌ You are not logged in"
    replies.add(text=text, quote=message)


def avatar_cmd(message: Message, replies: Replies) -> None:
    """Update your Mastodon avatar.

    In addition to this command, you must attach the avatar image you want to set.
    """
    if not message.filename:
        replies.add(
            text="❌ You must send an avatar attached to your message", quote=message
        )
        return

    masto = get_mastodon_from_msg(message)
    if masto:
        try:
            masto.account_update_credentials(avatar=message.filename)
            text = "✔️ Avatar updated"
        except mastodon.MastodonAPIError:
            text = "❌ Failed to update avatar"
    else:
        text = "❌ You are not logged in"
    replies.add(text=text, quote=message)


def dm_cmd(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    if not payload:
        replies.add(text="❌ Wrong usage", quote=message)
        return

    masto = get_mastodon_from_msg(message)
    if masto:
        username = payload.lstrip("@").lower()
        user = get_user(masto, username)
        if not user:
            replies.add(text=f"❌ Account not found: {username}", quote=message)
            return

        with session_scope() as session:
            acc = get_account_from_msg(message, session)
            assert acc
            dmchat = (
                session.query(DmChat)
                .filter_by(acc_addr=acc.addr, contact=user.acct)
                .first()
            )
            if dmchat:
                chat = bot.get_chat(dmchat.chat_id)
                replies.add(text="❌ Chat already exists, send messages here", chat=chat)
                return
            chat = bot.create_group(
                user.acct, bot.get_chat(acc.notifications).get_contacts()
            )
            session.add(DmChat(chat_id=chat.id, contact=user.acct, acc_addr=acc.addr))

        try:
            path = download_file(bot, user.avatar_static, ".jpg")
            chat.set_profile_image(path)
        except ValueError as err:
            bot.logger.exception(err)
            os.remove(path)
        except Exception as err:
            bot.logger.exception(err)
        replies.add(text=f"ℹ️ Private chat with: {user.acct}", chat=chat)
    else:
        replies.add(text="❌ You are not logged in", quote=message)


def reply_cmd(payload: str, message: Message, replies: Replies) -> None:
    """Reply to a toot with the given id."""
    args = payload.split(maxsplit=1)
    if len(args) != 2 and not (args and message.filename):
        replies.add(text="❌ Wrong usage", quote=message)
        return

    toot_id = args.pop(0)
    text = args.pop(0) if args else ""

    masto = get_mastodon_from_msg(message)
    if masto:
        send_toot(masto, text=text, filename=message.filename, in_reply_to=toot_id)
    else:
        replies.add(text="❌ You are not logged in", quote=message)


def star_cmd(payload: str, message: Message, replies: Replies) -> None:
    """Mark as favourite the toot with the given id."""
    if not payload:
        replies.add(text="❌ Wrong usage", quote=message)
    else:
        masto = get_mastodon_from_msg(message)
        if masto:
            masto.status_favourite(payload)
        else:
            replies.add(text="❌ You are not logged in", quote=message)


def boost_cmd(payload: str, message: Message, replies: Replies) -> None:
    """Boost the toot with the given id."""
    if not payload:
        replies.add(text="❌ Wrong usage", quote=message)
    else:
        masto = get_mastodon_from_msg(message)
        if masto:
            masto.status_reblog(payload)
        else:
            replies.add(text="❌ You are not logged in", quote=message)


def open_cmd(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    """Open the thread of the toot with the given id."""
    if not payload:
        replies.add(text="❌ Wrong usage", quote=message)
    else:
        masto = get_mastodon_from_msg(message)
        if masto:
            context = masto.status_context(payload)
            toots = (
                context["ancestors"] + [masto.status(payload)] + context["descendants"]
            )
            replies.add(
                text=TOOT_SEP.join(toots2texts(bot, toots))
                if toots
                else "❌ Nothing found",
                quote=message,
            )
        else:
            replies.add(text="❌ You are not logged in", quote=message)


def follow_cmd(payload: str, message: Message, replies: Replies) -> None:
    replies.add(
        text=account_action("account_follow", payload, message) or "✔️ User followed",
        quote=message,
    )


def unfollow_cmd(payload: str, message: Message, replies: Replies) -> None:
    replies.add(
        text=account_action("account_unfollow", payload, message)
        or "✔️ User unfollowed",
        quote=message,
    )


def mute_cmd(payload: str, message: Message, replies: Replies) -> None:
    if payload:
        replies.add(
            text=account_action("account_mute", payload, message) or "✔️ User muted",
            quote=message,
        )
        return
    # check if the message was sent in the Home chat
    with session_scope() as session:
        acc = session.query(Account).filter_by(home=message.chat.id).first()
        if acc:
            acc.muted_home = True
            replies.add(
                text="✔️ Home timeline muted",
                quote=message,
            )
        else:
            replies.add(
                text="❌ Wrong usage, you must send that command in the Home chat to mute it",
                quote=message,
            )


def unmute_cmd(payload: str, message: Message, replies: Replies) -> None:
    if payload:
        replies.add(
            text=account_action("account_unmute", payload, message)
            or "✔️ User unmuted",
            quote=message,
        )
        return
    # check if the message was sent in the Home chat
    with session_scope() as session:
        acc = session.query(Account).filter_by(home=message.chat.id).first()
        if acc:
            acc.muted_home = False
            replies.add(
                text="✔️ Home timeline unmuted",
                quote=message,
            )
        else:
            replies.add(
                text="❌ Wrong usage, you must send that command in the Home chat to unmute it",
                quote=message,
            )


def block_cmd(payload: str, message: Message, replies: Replies) -> None:
    replies.add(
        text=account_action("account_block", payload, message) or "✔️ User blocked",
        quote=message,
    )


def unblock_cmd(payload: str, message: Message, replies: Replies) -> None:
    replies.add(
        text=account_action("account_unblock", payload, message) or "✔️ User unblocked",
        quote=message,
    )


def profile_cmd(
    bot: DeltaBot, payload: str, message: Message, replies: Replies
) -> None:
    masto = get_mastodon_from_msg(message)
    if masto:
        text = get_profile(bot, masto, payload)
    else:
        text = "❌ You are not logged in"
    replies.add(text=text, quote=message)


def local_cmd(bot: DeltaBot, message: Message, replies: Replies) -> None:
    """Get latest entries from the local timeline."""
    masto = get_mastodon_from_msg(message)
    if masto:
        text = (
            TOOT_SEP.join(toots2texts(bot, reversed(masto.timeline_local())))
            or "❌ Nothing found"
        )
    else:
        text = "❌ You are not logged in"
    replies.add(text=text, quote=message)


def public_cmd(bot: DeltaBot, message: Message, replies: Replies) -> None:
    """Get latest entries from the public timeline."""
    masto = get_mastodon_from_msg(message)
    if masto:
        text = (
            TOOT_SEP.join(toots2texts(bot, reversed(masto.timeline_public())))
            or "❌ Nothing found"
        )
    else:
        text = "❌ You are not logged in"
    replies.add(text=text, quote=message)


def tag_cmd(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    if not payload:
        replies.add(text="❌ Wrong usage", quote=message)
        return

    tag = payload.lstrip("#")
    masto = get_mastodon_from_msg(message)
    if masto:
        text = (
            TOOT_SEP.join(toots2texts(bot, reversed(masto.timeline_hashtag(tag))))
            or "❌ Nothing found"
        )
    else:
        text = "❌ You are not logged in"
    replies.add(text=text, quote=message)


def search_cmd(bot: DeltaBot, payload: str, message: Message, replies: Replies) -> None:
    if not payload:
        replies.add(text="❌ Wrong usage", quote=message)
        return

    masto = get_mastodon_from_msg(message)
    if masto:
        res = masto.search(payload)
        prefix = getdefault(bot, "cmd_prefix", "")
        text = ""
        if res["accounts"]:
            text += "👤 Accounts:"
            for a in res["accounts"]:
                text += f"\n@{a.acct} /{prefix}profile_{a.id}"
            text += "\n\n"
        if res["hashtags"]:
            text += "#️⃣ Hashtags:"
            for tag in res["hashtags"]:
                text += f"\n#{tag.name} /{prefix}tag_{tag.name}"
        if not text:
            text = "❌ Nothing found"
    else:
        text = "❌ You are not logged in"
    replies.add(text=text, quote=message)

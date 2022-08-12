# Changelog

## [Unreleased]

### Added

- added `/profile` command to toots, to see the sender's profile.

### Changed

- send first attachment from toots as message attachment in home chat and direct conversations
- send each toot in home chat and each notification in notifications chat as individual messages.
- display toot sender as the impersonated message sender in Delta Chat, instead of including it in the message's text/body.
- tweaked toot layout

## [v0.3.0]

### Added

- OAuth2 support.

### Changed

- don't notify Mastodon server errors.
- tweaked informational message in the Home and Notifications chats when they are created.

### Fixed

- fix settings scope, settings under `simplebot_mastodon/` were not taking effect.

## [v0.2.0]

### Changed

- silently ignore messages in the notifications chat
- allow all members of a "Home", "Notifications" or direct messages chats to use the account (reply, star, boost, etc.)
- `/open` command now returns the full thread with ancestors and descendants
- show `/reply` and `/open` commands for direct messages

## v0.1.0

- initial release


[Unreleased]: https://github.com/adbenitez/deltachat-cursed/compare/v0.3.0...HEAD
[v0.3.0]: https://github.com/adbenitez/deltachat-cursed/compare/v0.2.0...v0.3.0
[v0.2.0]: https://github.com/adbenitez/deltachat-cursed/compare/v0.1.0...v0.2.0

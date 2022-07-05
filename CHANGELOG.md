# Changelog

## [Unreleased]

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


[Unreleased]: https://github.com/adbenitez/deltachat-cursed/compare/v0.2.0...HEAD
[v0.2.0]: https://github.com/adbenitez/deltachat-cursed/compare/v0.1.0...v0.2.0

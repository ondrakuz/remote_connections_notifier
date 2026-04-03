# Changelog

## Unreleased

- split Connection Notifier into a root collector and a user-session notifier
- added event queue delivery through `/run/connot/events`
- improved socket event details with visible peer, peer type, local endpoint, and target process when available
- added logging of enriched socket details
- added KDE delivery policy for persistent vs transient notifications
- reduced the set of persistent KDE-history notifications
- raised priority of the remaining persistent events and lowered urgency of non-critical chatter
- restored Bluetooth connect/disconnect as persistent KDE notifications
- set persistent notification timeout to 20 seconds and kept transient notifications at 5 seconds
- set aggregated socket burst notifications to a 30-second timeout
- promoted all KDE-history notifications to at least Warning severity
- removed persistent KDE notifications entirely
- set former persistent notifications to a 40-second transient timeout and kept other notifications at 5 seconds
- filtered the loopback network interface from NetworkManager notifications
- added an uninstall option to the installer script
- made event severity and delivery explicit in collector and notifier log messages
- updated installer for system and user services
- expanded project documentation and added operations playbook

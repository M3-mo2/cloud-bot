"""Development instruments: an architecture/design doctor and a bug reproducer.

This package is deliberately **offline**: it reads the source tree and the screen
catalogue, and it never starts a bot or opens a socket. ``doctor`` finds problems
the test suite does not (layer breaks, i18n drift, dead routes, design-rule
violations); ``reproduce`` turns a plain-language bug report into a stub failing
test. See ``docs/DEVTOOLS.md``.
"""

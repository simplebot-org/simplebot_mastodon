Mastodon/DeltaChat Bridge
=========================

.. image:: https://img.shields.io/pypi/v/simplebot_mastodon.svg
   :target: https://pypi.org/project/simplebot_mastodon

.. image:: https://img.shields.io/pypi/pyversions/simplebot_mastodon.svg
   :target: https://pypi.org/project/simplebot_mastodon

.. image:: https://pepy.tech/badge/simplebot_mastodon
   :target: https://pepy.tech/project/simplebot_mastodon

.. image:: https://img.shields.io/pypi/l/simplebot_mastodon.svg
   :target: https://pypi.org/project/simplebot_mastodon

.. image:: https://github.com/simplebot-org/simplebot_mastodon/actions/workflows/python-ci.yml/badge.svg
   :target: https://github.com/simplebot-org/simplebot_mastodon/actions/workflows/python-ci.yml

.. image:: https://img.shields.io/badge/code%20style-black-000000.svg
   :target: https://github.com/psf/black

A Mastodon/DeltaChat bridge plugin for `SimpleBot`_.

If this plugin has collisions with commands from other plugins in your bot, you can set a command prefix like ``/masto_`` for all commands::

  simplebot -a bot@example.com db -s simplebot_mastodon/cmd_prefix masto_

Install
-------

To install run::

  pip install simplebot-mastodon


.. _SimpleBot: https://github.com/simplebot-org/simplebot

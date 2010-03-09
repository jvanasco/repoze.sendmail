##############################################################################
#
# Copyright (c) 2001, 2002 Zope Corporation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################

from zope.component import queryUtility
from zope.component import getSiteManager
from zope.configuration.fields import Path
from zope.configuration.exceptions import ConfigurationError
from zope.interface import Interface
from zope.schema import TextLine
from zope.schema import BytesLine
from zope.schema import Int
from zope.schema import Bool

from repoze.sendmail.delivery import QueuedMailDelivery
from repoze.sendmail.delivery import DirectMailDelivery
from repoze.sendmail.queue import QueueProcessor
from repoze.sendmail.interfaces import IMailer
from repoze.sendmail.interfaces import IMailDelivery
from repoze.sendmail.mailer import SMTPMailer

def handler(methodName, *args, **kwargs):
    method = getattr(getSiteManager(), methodName)
    method(*args, **kwargs)

class IDeliveryDirective(Interface):
    """This abstract directive describes a generic mail delivery utility
    registration."""

    name = TextLine(
        title=u"Name",
        description=u'Specifies the Delivery name of the mail utility. '\
                    u'The default is "Mail".',
        default=u"Mail",
        required=False)

    mailer = TextLine(
        title=u"Mailer",
        description=u"Defines the mailer to be used for sending mail.",
        required=True)


class IQueuedDeliveryDirective(IDeliveryDirective):
    """This directive creates and registers a global queued mail utility. It
    should be only called once during startup."""

    queuePath = Path(
        title=u"Queue Path",
        description=u"Defines the path for the queue directory.",
        required=True)

def queuedDelivery(_context, queuePath, mailer, name="Mail"):

    def createQueuedDelivery():
        delivery = QueuedMailDelivery(queuePath)

        handler('registerUtility', delivery, IMailDelivery, name)

        mailerObject = queryUtility(IMailer, mailer)
        if mailerObject is None:
            raise ConfigurationError("Mailer %r is not defined" %mailer)

    _context.action(
            discriminator = ('delivery', name),
            callable = createQueuedDelivery,
            args = () )

class IDirectDeliveryDirective(IDeliveryDirective):
    """This directive creates and registers a global direct mail utility. It
    should be only called once during startup."""

def directDelivery(_context, mailer, name="Mail"):

    def createDirectDelivery():
        mailerObject = queryUtility(IMailer, mailer)
        if mailerObject is None:
            raise ConfigurationError("Mailer %r is not defined" %mailer)

        delivery = DirectMailDelivery(mailerObject)

        handler('registerUtility', delivery, IMailDelivery, name)

    _context.action(
            discriminator = ('utility', IMailDelivery, name),
            callable = createDirectDelivery,
            args = () )


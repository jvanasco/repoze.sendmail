##############################################################################
#
# Copyright (c) 2003 Zope Corporation and Contributors.
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
"""
Mail Delivery utility implementation

This module contains various implementations of Mail Deliveries.
"""

from email.message import Message
from email.header import Header
from email.parser import Parser
from email.utils import formatdate
from email.utils import make_msgid

from zope.interface import implementer
from repoze.sendmail.interfaces import IMailDelivery
from repoze.sendmail.maildir import Maildir
from repoze.sendmail import encoding
import transaction
from transaction.interfaces import ISavepointDataManager
from transaction.interfaces import IDataManagerSavepoint

import logging
log = logging.getLogger('repoze.sendmail.delivery')

class MailDataManagerState(object):
    """MailDataManagerState consolidates all the possible MDM and TPC states.
    This was modeled loosely after the Zope.Sqlaclhemy extension.
    """
    INIT = 0
    NO_WORK = 1
    COMMITTED = 2
    ABORTED = 3

    TPC_NONE = 10
    TPC_DIDBEGIN = 11
    TPC_VOTED = 12
    TPC_ABORTED  = 14
    TPC_FINISHED  = 15



@implementer(ISavepointDataManager)
class MailDataManager(object):
    """When creating a MailDataManager, we expect to :
        1. NOT be in a transaction on creation
        2. DO be joined into a transaction afterwards

        __init__ is given a `callable` function and `args` to pass into it.

        If everything goes as planned, during the tpc_finish phase we call:

            self.callable(*self.args)
    """
    def __init__(self, callable, args=(), onAbort=None,
                 transaction_manager=None):
        log.debug("MailDataManager.__init__")
        self.callable = callable
        self.args = args
        self.onAbort = onAbort
        if transaction_manager is None:
            transaction_manager = transaction.manager
        self.transaction_manager = transaction_manager
        self.transaction = None
        self.state = MailDataManagerState.INIT
        self.tpc_phase = MailDataManagerState.TPC_NONE
        
        
    def _transaction_ensure(self, trans=None):
        """shared function"""
        if self.transaction is None:
            raise ValueError("Not in a transaction")
        if self.transaction is not trans:
            raise ValueError("In a different transaction")


    def _finish(self, final_state, final_tpc_phase=None):
        """this just records the states"""
        if self.transaction is None:
            raise ValueError("Not in a transaction")    ## to pass : `test__finish_wo_transaction`
        self.state = final_state
        if final_tpc_phase is not None:
            self.tpc_phase = final_tpc_phase



    def join_transaction(self, trans=None):
        """Join the object into a transaction.

        If no transaction is specified, use ``transaction.manager.get()``.

        Raise an error if the object is already in a different transaction.
        """
        log.debug("MailDataManager.join_transaction")
        _before = self.transaction

        if trans is not None:
            _after = trans
        else:
            _after = self.transaction_manager.get()

        if _before is not None and _before is not _after:
            if self in _before._resources:
                raise ValueError("Item is in the former transaction. "
                        "It must be removed before it can be added "
                        "to a new transaction")

        if self not in _after._resources:
            _after.join(self)

        self.transaction = _after

    def commit(self, trans):
        log.debug("MailDataManager.commit")
        self._transaction_ensure(trans=trans)
        # OK to call ``commit`` w/ TPC underway

    def abort(self, trans):
        log.debug("MailDataManager.abort")
        self._transaction_ensure(trans=trans)
        if self.tpc_phase != MailDataManagerState.TPC_NONE :
            raise ValueError("TPC in progress")
        if self.onAbort:
            self.onAbort()

    def sortKey(self):
        return str(id(self))

    def savepoint(self):
        """Create a custom `MailDataSavepoint` object

        Although it has a `rollback` method, the custom instance doesn't
        actually do anything. `transaction` does it all.
        """
        log.debug("MailDataManager.savepoint")
        if self.transaction is None:
            raise ValueError("Not in a transaction")
        return MailDataSavepoint(self)


    def tpc_begin(self, trans, subtransaction=False):
        log.debug("MailDataManager.tpc_begin")
        self._transaction_ensure(trans=trans)
        if self.tpc_phase != MailDataManagerState.TPC_NONE :
            raise ValueError("TPC in progress")
        if subtransaction:
            raise ValueError("Subtransactions not supported")
        self.tpc_phase = MailDataManagerState.TPC_DIDBEGIN

    def tpc_vote(self, trans):
        log.debug("MailDataManager.tpc_vote")
        self._transaction_ensure(trans=trans)
        # for a one phase data manager commit last in tpc_vote
        if self.tpc_phase != MailDataManagerState.TPC_DIDBEGIN:
            raise ValueError("TPC phase error: got %d , expected %d" % 
                (self.tpc_phase, MailDataManagerState.TPC_DIDBEGIN) )
        self.state = MailDataManagerState.COMMITTED
        self.tpc_phase = MailDataManagerState.TPC_VOTED

    def tpc_finish(self, trans):
        log.debug("MailDataManager.tpc_finish")
        self._transaction_ensure(trans=trans)
        if self.tpc_phase != MailDataManagerState.TPC_VOTED:
            raise ValueError("TPC phase error: got %d , expected %d" % 
                (self.tpc_phase, MailDataManagerState.TPC_VOTED) )
        self.callable(*self.args)
        self._finish(MailDataManagerState.COMMITTED, MailDataManagerState.TPC_FINISHED)

    def tpc_abort(self, trans):
        log.debug("MailDataManager.tpc_abort")
        self._transaction_ensure(trans=trans)
        if self.tpc_phase == MailDataManagerState.TPC_NONE :
            raise ValueError("TPC phase error: got %d , expected !%d" % 
                (self.tpc_phase, MailDataManagerState.TPC_NONE) )
        elif self.tpc_phase == MailDataManagerState.TPC_FINISHED :
            raise ValueError("TPC already finished")        
        self._finish(MailDataManagerState.ABORTED, MailDataManagerState.TPC_ABORTED)


class TwoPhaseMailDataManager(MailDataManager):

    def tpc_vote(self, trans):
        log.debug("TwoPhaseMailDataManager.tpc_vote")
        if self.transaction is not None:  # there may have been no work to do
            self._finish( MailDataManagerState.VOTED, MailDataManagerState.TPC_VOTED )

    def tpc_finish(self, trans):
        log.debug("TwoPhaseMailDataManager.tpc_finish")
        if self.transaction is not None:  # there may have been no work to do
            self._finish( MailDataManagerState.COMMITTED, MailDataManagerState.TPC_FINISHED )

    def tpc_abort(self, trans):
        log.debug("TwoPhaseMailDataManager.tpc_abort")
        if self.transaction is not None:  # we may not have voted, and been aborted already
            self._finish( MailDataManagerState.ABORTED, MailDataManagerState.TPC_ABORTED )



@implementer(IDataManagerSavepoint)
class MailDataSavepoint:
    """Don't actually do anything; transaction does it all.
    """
    def __init__(self, mail_data_manager):
        log.debug("MailDataSavepoint.__init__")
        pass

    def rollback(self):
        pass


class AbstractMailDelivery(object):
    """Base class for mail delivery.

    Calling ``send`` will create a managed message -- the result of
    ``self.createDataManager(fromaddr,toaddrs,message)``

    The managed message should be an instance of `MailDataManager` or
    another class that implements `IDataManager` or `ISavepointDataManager`

    The managed message is immediately joined into the current transaction.
    """
    def send(self, fromaddr, toaddrs, message):
        if not isinstance(message, Message):
            raise ValueError('Message must be email.message.Message')
        encoding.cleanup_message(message)
        messageid = message['Message-Id']
        if messageid is None:
            messageid = message['Message-Id'] = make_msgid('repoze.sendmail')
        if message['Date'] is None:
            message['Date'] = formatdate()
        managedMessage = self.createDataManager(fromaddr, toaddrs, message)
        managedMessage.join_transaction()
        return messageid


@implementer(IMailDelivery)
class DirectMailDelivery(AbstractMailDelivery):

    def __init__(self, mailer, transaction_manager=None):
        self.mailer = mailer
        if transaction_manager is None:
            transaction_manager = transaction.manager
        self.transaction_manager = transaction_manager

    def createDataManager(self, fromaddr, toaddrs, message):
        return MailDataManager(self.mailer.send,
                               args=(fromaddr, toaddrs, message),
                               transaction_manager=self.transaction_manager)


@implementer(IMailDelivery)
class QueuedMailDelivery(AbstractMailDelivery):

    queuePath = property(lambda self: self._queuePath)
    processor_thread = None

    def __init__(self, queuePath, transaction_manager=None):
        self._queuePath = queuePath
        if transaction_manager is None:
            transaction_manager = transaction.manager
        self.transaction_manager = transaction_manager

    def createDataManager(self, fromaddr, toaddrs, message):
        message = copy_message(message)
        message['X-Actually-From'] = Header(fromaddr, 'utf-8')
        message['X-Actually-To'] = Header(','.join(toaddrs), 'utf-8')
        maildir = Maildir(self.queuePath, True)
        tx_message = maildir.add(message)
        return MailDataManager(tx_message.commit, onAbort=tx_message.abort,
                               transaction_manager=self.transaction_manager)


def copy_message(message):
    parser = Parser()
    return parser.parsestr(message.as_string())

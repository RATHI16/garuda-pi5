"""Control-owned desired selection, delivered outside the control timer.

A generation survives a skipped clear: releasing then reselecting the same integer
ID is still a new Pi limiter session. Producer state may lag this intent; consumers
must reject old generations immediately, without waiting for network confirmation.
"""
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class OnboardSelection:
    def __init__(self, opener=urlopen):
        self.lock=threading.Lock();self.sent=None;self.opener=opener;self.failure=None;self.error=None
        self.desired=dict(onboard_select=None,selection_gen=0,epoch=None,instance=None)

    def publish(self, core, active):
        intent=dict(onboard_select=core.target_id if active and not core.requires_reselection else None,
                    selection_gen=core.selection_generation,epoch=core.epoch,instance=core.instance)
        with self.lock:self.desired=intent

    def sync(self, source, opener=None):
        if source['kind']!='onboard':return
        with self.lock:intent=dict(self.desired)
        key=(source['generation'],source['url'],tuple(intent.items()))
        if key==self.sent:return
        req=Request(source['url']+'/api/config',data=json.dumps(intent).encode(),headers={'Content-Type':'application/json'})
        try:
            with (opener or self.opener)(req,timeout=.6) as r:
                if json.load(r).get('ok') is not True:raise ValueError('Invalid selection acknowledgement')
        except HTTPError as exc:
            if exc.code not in (400,409):
                with self.lock:self.error='Selection transport HTTP '+str(exc.code)
                raise
            try:reason=json.loads(exc.read(8192)).get('error','selection refused')
            except Exception:reason='HTTP '+str(exc.code)
            why='Onboard selection refused: '+str(reason)
            with self.lock:
                self.error=why
                self.failure=dict(source_generation=source['generation'],selection_gen=intent['selection_gen'],
                                  target=intent['onboard_select'],reason=why)
            self.sent=key  # definitive refusal is an outcome, not a retry loop
            return
        except Exception as exc:
            with self.lock:self.error='Selection transport unavailable: '+type(exc).__name__
            raise
        with self.lock:self.error=None
        self.sent=key  # a newer intent remains unsent and will be delivered next iteration

    def consume_refusal(self, source, core):
        with self.lock:failure=self.failure;self.failure=None
        if (failure and source['kind']=='onboard' and failure['source_generation']==source['generation']
            and failure['selection_gen']==core.selection_generation and failure['target']==core.target_id
            and core.target_id is not None):
            core.boundary(failure['reason'])
            return failure['reason']
        return None

    def state(self):
        with self.lock:return {'error':self.error}


def read_with_selection(reader, selection, source):
    # A selection refusal must not starve the stream needed to recover/select again.
    try:selection.sync(source)
    except Exception:pass  # transport failure remains diagnostic and is retried next iteration
    return reader.read(source)


def current_command(core):
    o=core.snapshot.get('onboard')
    if (core.target_id is None or core.requires_reselection or not isinstance(o,dict)
        or o.get('target_id')!=core.target_id or o.get('selection_gen')!=core.selection_generation):
        return None
    return o

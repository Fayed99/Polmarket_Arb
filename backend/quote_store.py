"""Durable, scoped quote binding. A quote can only be claimed once."""
import copy
import hashlib
import json
import time
import uuid
from fastapi import HTTPException
from pymongo import ReturnDocument


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def market_binding(market):
    return {'id': market['id'], 'condition_id': market['condition_id'], 'start': market['start'],
            'end': market['end'], 'fee': market['fee'], 'tokens': {
                s: {k: market['books'][s][k] for k in ('token_id','tick','minimum','size_precision')}
                for s in ('up','down')}}


class QuoteStore:
    def __init__(self, db):
        self.collection = db.quotes

    async def issue(self, session, market, kind, payload):
        now = time.time()
        record = {**copy.deepcopy(payload), 'quote_id': str(uuid.uuid4()), 'session_id': session['id'],
                  'kind': kind, 'state': 'READY', 'created': now,
                  'expires_at': min(now+10, market['end']), 'binding': market_binding(market),
                  'settings_hash': digest(session['settings'].model_dump()),
                  'settings_snapshot': session['settings'].model_dump(),
                  'book_evidence': {s:{k:market['books'][s].get(k) for k in ('hash','event_ms','arrival_ms')}
                                    for s in ('up','down')}}
        await self.collection.insert_one(copy.deepcopy(record))
        return {k:v for k,v in record.items() if k != 'session_id'}

    async def load(self, session, quote_id, kind):
        quote = await self.collection.find_one({'session_id':session['id'], 'quote_id':quote_id}, {'_id':0})
        if not quote or quote.get('kind') != kind:
            raise HTTPException(409, 'Invalid quote type or workspace. Request a fresh quote.')
        if quote['expires_at'] <= time.time():
            raise HTTPException(409, 'Quote expired. Request a fresh quote.')
        if quote['state'] != 'READY':
            raise HTTPException(409, 'Quote already used or invalidated. Request a fresh quote.')
        if quote['settings_hash'] != digest(session['settings'].model_dump()):
            raise HTTPException(409, 'Settings changed. Request a fresh quote.')
        return quote

    async def claim(self, session, quote, request_id, execution_id):
        claimed = await self.collection.find_one_and_update(
            {'quote_id':quote['quote_id'], 'session_id':session['id'], 'state':'READY',
             'expires_at':{'$gt':time.time()}, 'settings_hash':digest(session['settings'].model_dump())},
            {'$set':{'state':'CLAIMED', 'request_id':request_id, 'execution_id':execution_id,
                     'claimed_at':time.time()}}, projection={'_id':0}, return_document=ReturnDocument.AFTER)
        if not claimed:
            raise HTTPException(409, 'Quote expired or already claimed. No new trade submitted.')

    async def finish(self, session, quote_id, status):
        await self.collection.update_one({'quote_id':quote_id, 'session_id':session['id'], 'state':'CLAIMED'},
                                         {'$set':{'state':'CONSUMED', 'execution_status':status}})

    async def invalidate(self, session_id):
        await self.collection.update_many({'session_id':session_id, 'state':'READY'}, {'$set':{'state':'INVALIDATED'}})
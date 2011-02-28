# -*- coding: utf-8 -*-
# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU GPL, version 3 or later; http://www.gnu.org/copyleft/gpl.html

DECK_VERSION = 100

import time, simplejson
from anki.db import *
from anki.lang import _
from anki.media import rebuildMediaDir
from anki.tags import initTagTables

def moveTable(s, table):
    sql = s.scalar(
        "select sql from sqlite_master where name = '%s'" % table)
    sql = sql.replace("TABLE "+table, "temporary table %s2" % table)
    s.execute(sql)
    s.execute("insert into %s2 select * from %s" % (table, table))
    s.execute("drop table "+table)

def upgradeSchema(engine, s):
    "Alter tables prior to ORM initialization."
    try:
        ver = s.scalar("select version from deck limit 1")
    except:
        ver = s.scalar("select version from decks limit 1")
    if ver < 65:
        raise Exception("oldDeckVersion")
    if ver < 99:
        # fields
        ###########
        s.execute(
            "alter table fields add column chksum text not null default ''")
        # cards
        ###########
        moveTable(s, "cards")
        import cards
        metadata.create_all(engine, tables=[cards.cardsTable])
        s.execute("""
insert into cards select id, factId,
(select modelId from facts where facts.id = cards2.factId),
cardModelId, created, modified,
question, answer, ordinal, 0, relativeDelay, type, due, interval,
factor, reps, successive, noCount, 0, 0 from cards2""")
        s.execute("drop table cards2")
        # tags
        ###########
        moveTable(s, "tags")
        moveTable(s, "cardTags")
        initTagTables(s)
        s.execute("insert or ignore into tags select id, tag, 0 from tags2")
        s.execute("""
insert or ignore into cardTags select cardId, tagId, src from cardTags2""")
        s.execute("drop table tags2")
        s.execute("drop table cardTags2")
        # facts
        ###########
        moveTable(s, "facts")
        import facts
        metadata.create_all(engine, tables=[facts.factsTable])
        s.execute("""
insert or ignore into facts select id, modelId, created, modified, tags,
spaceUntil from facts2""")
        s.execute("drop table facts2")
        # media
        ###########
        moveTable(s, "media")
        import media
        metadata.create_all(engine, tables=[media.mediaTable])
        s.execute("""
insert or ignore into media select id, filename, size, created,
originalPath from media2""")
        s.execute("drop table media2")
        # deck
        ###########
        migrateDeck(s, engine)
        # models
        ###########
        moveTable(s, "models")
        import models
        metadata.create_all(engine, tables=[models.modelsTable])
        s.execute("""
insert or ignore into models select id, created, modified, name,
:c from models2""", {'c':simplejson.dumps(models.defaultConf)})
        s.execute("drop table models2")

    return ver

def migrateDeck(s, engine):
    import deck
    metadata.create_all(engine, tables=[deck.deckTable])
    s.execute("""
insert into deck select id, created, modified, 0, 99,
ifnull(syncName, ""), lastSync, utcOffset, "", "", "" from decks""")
    # update selective study
    lim = deck.defaultLim.copy()
    keys = ("newActive", "newInactive", "revActive", "revInactive")
    for k in keys:
        lim[k] = s.execute("select value from deckVars where key=:k",
                           {'k':k}).scalar()
        s.execute("delete from deckVars where key=:k", {'k':k})
    # fetch remaining settings from decks table
    conf = deck.defaultConf.copy()
    data = {}
    keys = ("newCardOrder", "newCardSpacing", "newCardsPerDay",
            "revCardOrder", "sessionRepLimit", "sessionTimeLimit")
    for k in keys:
        conf[k] = s.execute("select %s from decks" % k).scalar()
    # add any deck vars and save
    dkeys = ("hexCache", "cssCache")
    for (k, v) in s.execute("select * from deckVars").fetchall():
        if k in dkeys:
            data[k] = v
        else:
            conf[k] = v
    s.execute("update deck set limits = :l, config = :c, data = :d",
              {'l':simplejson.dumps(lim),
               'c':simplejson.dumps(conf),
               'd':simplejson.dumps(data)})
    # clean up
    s.execute("drop table decks")
    s.execute("drop table deckVars")

def updateIndices(db):
    "Add indices to the DB."
    # due counts, failed card queue
    db.execute("""
create index if not exists ix_cards_queueDue on cards
(queue, due, factId)""")
    # counting cards of a given type
    db.execute("""
create index if not exists ix_cards_type on cards
(type)""")
    # sync summaries
    db.execute("""
create index if not exists ix_cards_modified on cards
(modified)""")
    db.execute("""
create index if not exists ix_facts_modified on facts
(modified)""")
    # card spacing
    db.execute("""
create index if not exists ix_cards_factId on cards (factId)""")
    # fields
    db.execute("""
create index if not exists ix_fields_factId on fields (factId)""")
    db.execute("""
create index if not exists ix_fields_chksum on fields (chksum)""")
    # media
    db.execute("""
create index if not exists ix_media_chksum on media (chksum)""")
    # deletion tracking
    db.execute("""
create index if not exists ix_cardsDeleted_cardId on cardsDeleted (cardId)""")
    db.execute("""
create index if not exists ix_modelsDeleted_modelId on modelsDeleted (modelId)""")
    db.execute("""
create index if not exists ix_factsDeleted_factId on factsDeleted (factId)""")
    db.execute("""
create index if not exists ix_mediaDeleted_factId on mediaDeleted (mediaId)""")
    # tags
    db.execute("""
create index if not exists ix_cardTags_cardId on cardTags (cardId)""")

def upgradeDeck(deck):
    "Upgrade deck to the latest version."
    if deck.version < DECK_VERSION:
        prog = True
        deck.startProgress()
        deck.updateProgress(_("Upgrading Deck..."))
        oldmod = deck.modified
    else:
        prog = False
    if deck.version < 100:
        # update dynamic indices given we don't use priority anymore
        for d in ("intervalDesc", "intervalAsc", "randomOrder",
                  "dueAsc", "dueDesc"):
            deck.db.statement("drop index if exists ix_cards_%s2" % d)
            deck.db.statement("drop index if exists ix_cards_%s" % d)
        deck.updateDynamicIndices()
        # remove old views
        for v in ("failedCards", "revCardsOld", "revCardsNew",
                  "revCardsDue", "revCardsRandom", "acqCardsRandom",
                  "acqCardsOld", "acqCardsNew"):
            deck.db.statement("drop view if exists %s" % v)
        # remove the expensive value cache
        deck.db.statement("drop index if exists ix_fields_value")
        # add checksums and index
        deck.updateAllFieldChecksums()
        # this was only used for calculating average factor
        deck.db.statement("drop index if exists ix_cards_factor")
        # remove stats, as it's all in the revlog now
        deck.db.statement("drop table if exists stats")
        # migrate revlog data to new table
        deck.db.statement("""
insert or ignore into revlog select
cast(time*1000 as int), cardId, ease, reps, lastInterval, nextInterval, nextFactor,
cast(min(thinkingTime, 60)*1000 as int), 0 from reviewHistory""")
        deck.db.statement("drop table reviewHistory")
        # convert old ease0 into ease1
        deck.db.statement("update revlog set ease = 1 where ease = 0")
        # remove priority index
        deck.db.statement("drop index if exists ix_cards_priority")
        # suspended cards don't use ranges anymore
        deck.db.execute("update cards set queue=-1 where queue between -3 and -1")
        deck.db.execute("update cards set queue=-2 where queue between 3 and 5")
        deck.db.execute("update cards set queue=-3 where queue between 6 and 8")
        # don't need an index on fieldModelId
        deck.db.statement("drop index if exists ix_fields_fieldModelId")
        # update schema time
        deck.db.statement("update deck set schemaMod = :t", t=time.time())

        # finally, update indices & optimize
        updateIndices(deck.db)
        deck.db.execute("vacuum")
        deck.db.execute("analyze")
        deck.version = 100
        deck.db.commit()
    if prog:
        assert deck.modified == oldmod
        deck.finishProgress()

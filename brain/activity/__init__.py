"""Activity log (Observabilidade V2): what Juniper did, in sentences anyone can search.

``recorder`` listens to the ``event_log`` and writes one record per action; ``catalog`` says what
each event means; ``model`` is the record; the store is ``brain/memory/activity.py``. The legacy
logs are untouched: this is a layer beside them.
"""

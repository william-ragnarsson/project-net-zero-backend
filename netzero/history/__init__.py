"""Optional Postgres history: every run's events in one append-only store.

``runs/<id>/events.jsonl`` stays what the pipeline writes. With
``NETZERO_DATABASE_URL`` set, the server ships new lines into Postgres
(``ingest``), folds them into read models (``projector``) and serves them
under ``/api/history`` (``queries``). Without it nothing here runs.
"""

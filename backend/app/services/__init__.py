"""Business-logic layer, separate from both the HTTP layer (`app.api`) and the
raw ORM models (`app.models`). A service function takes plain, already-validated
data plus a database session, and returns/raises in terms the API layer can
translate directly into a response -- it doesn't know about FastAPI at all.
"""

from models import Paper


def paper_work_counts(db):
    pending = db.query(Paper).filter(Paper.processed == False, Paper.error == None).count()
    failed = db.query(Paper).filter(Paper.processed == False, Paper.error != None).count()
    return {"pending": pending, "failed_count": failed, "unprocessed": pending + failed}

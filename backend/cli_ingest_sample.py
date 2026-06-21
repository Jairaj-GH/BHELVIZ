"""Simple CLI to ingest one or more text files into ChromaDB using ingestion helpers.

Usage:
  python cli_ingest_sample.py [path/to/file.txt] [...]

If no path is provided, a small sample policy text will be ingested.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent

def ingest_paths(paths):
    from ingestion import _init_resources, _chunk_text, _embed_texts, CHROMA_DIR, COLLECTION_NAME
    print('Initializing embedding and ChromaDB...')
    _init_resources()

    # Build chunks from each path
    ids = []
    texts = []
    metadatas = []

    for p in paths:
        p = Path(p)
        if not p.exists():
            print('Skipping missing', p)
            continue
        txt = p.read_text(encoding='utf-8', errors='ignore')
        chunks = _chunk_text(txt)
        for i, c in enumerate(chunks):
            doc_id = f"{p.name}::{i}::{os.urandom(4).hex()}"
            ids.append(doc_id)
            texts.append(c)
            metadatas.append({'filename': p.name, 'page': 1, 'chunk_id': i})

    if not texts:
        print('No texts to ingest')
        return

    print(f'Embedding {len(texts)} chunks...')
    embs = _embed_texts(texts)

    # Upsert into Chroma
    import chromadb
    from chromadb.config import Settings
    client = chromadb.PersistentClient(
        path=CHROMA_DIR
    )
    col = client.get_or_create_collection(name=COLLECTION_NAME)
    col.add(ids=ids, documents=texts, metadatas=metadatas, embeddings=embs)
    try:
        if hasattr(client, 'persist'):
            client.persist()
    except Exception:
        pass

    print('Ingested', len(ids), 'chunks into collection', COLLECTION_NAME)


def main():
    args = sys.argv[1:]
    if not args:
        # create sample file
        sample = REPO.parent / 'sample_doc.txt'
        sample.write_text("""
BHELVIZ Policy Document

Company leave policy:
- Employees are entitled to 18 days of paid leave per year.
- Leaves must be approved by manager; medical leaves require certificate.
- For the Production department, additional safety training is required before extended leave.

Attendance recording:
- Late mark applies after 9:15 AM.
- False present detection uses biometric and badge logs.
""")
        paths = [str(sample)]
    else:
        paths = args

    ingest_paths(paths)


if __name__ == '__main__':
    main()

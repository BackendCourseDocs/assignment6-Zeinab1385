from fastapi import FastAPI, Query, HTTPException, File, UploadFile, Form
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel
from typing import List, Optional
import math
import shutil
import os
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI(
    title="Book Search Engine Pro",
    description="سیستم مدیریت کتاب متصل به PostgreSQL با گزارش‌دهی زنده",
    version="3.5.0"
)

# --- تنظیمات زیرساختی ---
UPLOAD_DIR = "static/images"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

BASE_URL = "https://openlibrary.org/search.json"


# تابع اتصال به دیتابیس با پسورد 1234
def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="book_db",
        user="postgres",
        password="1234"  # <--- پسورد به درخواست شما تغییر کرد
    )


# --- ۱. اضافه کردن کتاب (CREATE) ---
@app.post("/add-book", tags=["مدیریت کتاب"])
async def create_book(
        title: str = Form(..., min_length=3),
        author: str = Form(..., min_length=2),
        publisher: str = Form("نامشخص"),
        year: Optional[int] = Form(None),
        file: UploadFile = File(...)
):
    print(f"\n>>> درخواست اضافه کردن کتاب جدید: {title}")
    book_id = str(uuid.uuid4())
    file_extension = os.path.splitext(file.filename)[1]
    unique_filename = f"{book_id}{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    image_url = f"http://127.0.0.1:8000/static/images/{unique_filename}"

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = "INSERT INTO books (id, title, author, publisher, year, image, internal_path) VALUES (%s, %s, %s, %s, %s, %s, %s)"
        cur.execute(query, (book_id, title, author, publisher, year, image_url, file_path))
        conn.commit()
        cur.close()
        conn.close()
        print(f"--- موفقیت: کتاب در دیتابیس ذخیره شد.")
        return {"status": "موفقیت‌آمیز", "id": book_id}
    except Exception as e:
        print(f"--- خطا در ذخیره‌سازی: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- ۲. ویرایش کتاب (PATCH) ---
@app.patch("/books/{book_id}", tags=["مدیریت کتاب"])
async def update_book(
        book_id: str,
        title: Optional[str] = Form(None),
        author: Optional[str] = Form(None),
        publisher: Optional[str] = Form(None),
        year: Optional[int] = Form(None)
):
    print(f"\n>>> درخواست ویرایش کتاب: {book_id}")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        if title: cur.execute("UPDATE books SET title = %s WHERE id = %s", (title, book_id))
        if author: cur.execute("UPDATE books SET author = %s WHERE id = %s", (author, book_id))
        if publisher: cur.execute("UPDATE books SET publisher = %s WHERE id = %s", (publisher, book_id))
        if year: cur.execute("UPDATE books SET year = %s WHERE id = %s", (year, book_id))

        conn.commit()
        cur.close()
        conn.close()
        print("--- تغییرات اعمال شد.")
        return {"status": "ویرایش انجام شد"}
    except Exception as e:
        print(f"--- خطا در ویرایش: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- ۳. حذف کتاب (DELETE) ---
@app.delete("/books/{book_id}", tags=["مدیریت کتاب"])
async def delete_book(book_id: str):
    print(f"\n>>> درخواست حذف کتاب: {book_id}")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT internal_path FROM books WHERE id = %s", (book_id,))
        result = cur.fetchone()

        if result and os.path.exists(result[0]):
            os.remove(result[0])

        cur.execute("DELETE FROM books WHERE id = %s", (book_id,))
        conn.commit()
        cur.close()
        conn.close()
        print("--- کتاب حذف شد.")
        return {"status": "کتاب حذف شد"}
    except Exception as e:
        print(f"--- خطا در حذف: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- ۴. جستجو (SEARCH) ---
@app.get("/search", tags=["جستجو"])
async def search_books(
        q: str = Query(..., min_length=3),
        page: int = Query(1, ge=1),
        size: int = Query(10, ge=1, le=50)
):
    results = []
    search_q = q.lower()
    print(f"\n>>> جستجو برای: '{search_q}'")

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # استفاده از دستور SELECT برای استخراج داده
        sql_query = "SELECT id, title, author, publisher, year, image FROM books WHERE LOWER(title) LIKE %s OR LOWER(author) LIKE %s"
        cur.execute(sql_query, (f"%{search_q}%", f"%{search_q}%"))
        db_books = cur.fetchall()
        results.extend(db_books)
        print(f"--- تعداد {len(db_books)} کتاب از دیتابیس پیدا شد.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"--- خطا در دیتابیس: {e}")

    # سرچ در OpenLibrary
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(BASE_URL, params={"q": q, "limit": 20})
            data = response.json()
            for doc in data.get("docs", []):
                results.append({
                    "id": f"ol_{doc.get('edition_key', [uuid.uuid4()])[0]}",
                    "title": doc.get("title"),
                    "author": ", ".join(doc.get("author_name", ["نامشخص"])),
                    "publisher": doc.get("publisher", ["نامشخص"])[0],
                    "year": doc.get("first_publish_year"),
                    "image": f"https://covers.openlibrary.org/b/id/{doc.get('cover_i')}-L.jpg" if doc.get(
                        'cover_i') else None
                })
        except:
            pass

    start = (page - 1) * size
    return {
        "metadata": {"total": len(results), "page": page, "total_pages": math.ceil(len(results) / size)},
        "books": results[start: start + size]
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
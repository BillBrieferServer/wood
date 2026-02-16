import os
import re
import base64
import secrets
import shutil
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from contextlib import contextmanager

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from database import get_db, init_db

app = FastAPI(title="Hardwood Haven of Idaho")

SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "hardwood2024")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.ionos.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
QUOTE_EMAIL = os.environ.get("QUOTE_EMAIL", "alanhardwoodhaven@gmail.com")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

logger = logging.getLogger("uvicorn.error")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "static", "images", "products")

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


# --- CSRF Protection ---

def get_csrf_token(request: Request) -> str:
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)
    return request.session["csrf_token"]


def validate_csrf(request: Request, csrf_token: str):
    session_token = request.session.get("csrf_token", "")
    if not csrf_token or not session_token or not secrets.compare_digest(session_token, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


@contextmanager
def db():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text


@app.on_event("startup")
def startup():
    init_db()
    os.makedirs(UPLOAD_DIR, exist_ok=True)


# --- Public Routes ---

@app.get("/", response_class=HTMLResponse)
def homepage(request: Request):
    with db() as conn:
        page = conn.execute("SELECT * FROM pages WHERE slug = 'home'").fetchone()
        featured = conn.execute(
            "SELECT p.*, pi.image_path FROM products p LEFT JOIN product_images pi ON p.id = pi.product_id AND pi.sort_order = 0 ORDER BY p.id DESC LIMIT 6"
        ).fetchall()
    return templates.TemplateResponse("home.html", {
        "request": request, "page": page, "featured": featured
    })


@app.get("/shop", response_class=HTMLResponse)
def shop(request: Request, category: str = ""):
    with db() as conn:
        categories = conn.execute(
            "SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category"
        ).fetchall()

        if category:
            products = conn.execute(
                """SELECT p.*, pi.image_path FROM products p
                   LEFT JOIN product_images pi ON p.id = pi.product_id AND pi.sort_order = 0
                   WHERE p.category = ? ORDER BY p.name""",
                (category,)
            ).fetchall()
        else:
            products = conn.execute(
                """SELECT p.*, pi.image_path FROM products p
                   LEFT JOIN product_images pi ON p.id = pi.product_id AND pi.sort_order = 0
                   ORDER BY p.name"""
            ).fetchall()

    return templates.TemplateResponse("shop.html", {
        "request": request, "products": products,
        "categories": categories, "current_category": category
    })


@app.get("/product/{slug}", response_class=HTMLResponse)
def product_detail(request: Request, slug: str):
    with db() as conn:
        product = conn.execute("SELECT * FROM products WHERE slug = ?", (slug,)).fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        images = conn.execute(
            "SELECT * FROM product_images WHERE product_id = ? ORDER BY sort_order",
            (product["id"],)
        ).fetchall()
    return templates.TemplateResponse("product.html", {
        "request": request, "product": product, "images": images
    })


# Content pages
def _get_species():
    return [
        "Alder", "Ambrosia Maple", "American Beech", "American Elm",
        "American Sycamore", "Black Cherry", "Black Walnut",
        "Butternut Hickory", "Carolina Hickory", "Chestnut",
        "Flaming Box Elder", "Flowering Dogwood", "Green Ash",
        "Live Oak", "Northern Red Oak", "Pecan", "Post Oak",
        "Rainbow Poplar", "Red Maple", "Shagbark Hickory",
        "Shellbark Hickory", "Sugar Maple", "Water Oak",
        "White Ash", "White Oak", "Yellow Poplar",
    ]


@app.get("/quote", response_class=HTMLResponse)
def quote(request: Request):
    return templates.TemplateResponse("quote.html", {
        "request": request, "success": False, "error": "",
        "form_data": {}, "species_list": _get_species(),
        "csrf_token": get_csrf_token(request)
    })


@app.post("/quote", response_class=HTMLResponse)
async def quote_submit(
    request: Request,
    csrf_token: str = Form(""),
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    species: str = Form(""),
    project_type: str = Form(""),
    size: str = Form(""),
    timeline: str = Form(""),
    delivery: str = Form(""),
    details: str = Form(""),
):
    form_data = {
        "name": name, "email": email, "phone": phone,
        "species": species, "project_type": project_type,
        "size": size, "timeline": timeline,
        "delivery": delivery, "details": details,
    }

    validate_csrf(request, csrf_token)

    # Validate required fields
    if not name.strip() or not email.strip():
        return templates.TemplateResponse("quote.html", {
            "request": request, "success": False,
            "error": "Name and email are required.",
            "form_data": form_data, "species_list": _get_species(),
            "csrf_token": get_csrf_token(request),
        })

    # Build email body
    lines = [
        f"Name: {name}",
        f"Email: {email}",
        f"Phone: {phone or 'Not provided'}",
        f"Species: {species or 'Not specified'}",
        f"Project type: {project_type or 'Not specified'}",
        f"Size: {size or 'Not specified'}",
        f"Timeline: {timeline or 'Not specified'}",
        f"Delivery: {delivery or 'Not specified'}",
        f"Details: {details or 'None'}",
    ]
    body_text = chr(10).join(lines)

    # Send email
    email_sent = False
    if SMTP_USER and SMTP_PASS:
        try:
            msg = MIMEMultipart()
            msg["From"] = f"Hardwood Haven <{SMTP_USER}>"
            msg["To"] = QUOTE_EMAIL
            msg["Reply-To"] = email
            msg["Subject"] = f"Quote Request from {name}"
            msg.attach(MIMEText("New quote request from the website:\n\n" + body_text, "plain"))
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            email_sent = True
        except Exception as e:
            logger.error(f"Quote email failed: {e}")

    # Send ntfy notification
    if NTFY_TOPIC:
        try:
            import httpx
            ntfy_body = "Quote from " + name + chr(10) + (project_type or "No project type") + " - " + (species or "No species") + chr(10) + "Email: " + email
            httpx.post(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                content=ntfy_body.encode(),
                headers={"Title": "New Quote Request", "Priority": "high", "Tags": "wood,incoming_envelope"},
            )
        except Exception as e:
            logger.error(f"Quote ntfy failed: {e}")

    if not email_sent and SMTP_USER:
        return templates.TemplateResponse("quote.html", {
            "request": request, "success": False,
            "error": "Something went wrong sending your request. Please call or email Alan directly.",
            "form_data": form_data, "species_list": _get_species(),
            "csrf_token": get_csrf_token(request),
        })

    return templates.TemplateResponse("quote.html", {
        "request": request, "success": True, "error": "",
        "form_data": {}, "species_list": _get_species(),
        "csrf_token": get_csrf_token(request),
    })

@app.get("/our-story", response_class=HTMLResponse)
def our_story(request: Request):
    return _render_page(request, "our-story")

@app.get("/resources", response_class=HTMLResponse)
def resources(request: Request):
    return _render_page(request, "resources")

@app.get("/returns", response_class=HTMLResponse)
def returns(request: Request):
    return _render_page(request, "returns")

@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return _render_page(request, "privacy")


def _render_page(request: Request, slug: str):
    with db() as conn:
        page = conn.execute("SELECT * FROM pages WHERE slug = ?", (slug,)).fetchone()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return templates.TemplateResponse("page.html", {"request": request, "page": page})


# --- Admin Routes ---

def is_admin(request: Request) -> bool:
    return request.session.get("admin") is True


@app.get("/admin", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if is_admin(request):
        return RedirectResponse("/admin/products", status_code=302)
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": "", "csrf_token": get_csrf_token(request)})


@app.post("/admin", response_class=HTMLResponse)
def admin_login(request: Request, password: str = Form(...), csrf_token: str = Form("")):
    validate_csrf(request, csrf_token)
    if password == ADMIN_PASSWORD:
        request.session["admin"] = True
        return RedirectResponse("/admin/products", status_code=302)
    return templates.TemplateResponse("admin/login.html", {
        "request": request, "error": "Invalid password",
        "csrf_token": get_csrf_token(request)
    })


@app.get("/admin/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin", status_code=302)


@app.get("/admin/products", response_class=HTMLResponse)
def admin_products(request: Request):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    with db() as conn:
        products = conn.execute(
            """SELECT p.*, pi.image_path FROM products p
               LEFT JOIN product_images pi ON p.id = pi.product_id AND pi.sort_order = 0
               ORDER BY p.name"""
        ).fetchall()
    return templates.TemplateResponse("admin/products.html", {
        "request": request, "products": products,
        "csrf_token": get_csrf_token(request)
    })


@app.get("/admin/products/new", response_class=HTMLResponse)
def admin_product_new(request: Request):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("admin/product_form.html", {
        "request": request, "product": None, "images": [],
        "has_ai": bool(ANTHROPIC_API_KEY), "auto_generate": False,
        "csrf_token": get_csrf_token(request)
    })


@app.get("/admin/products/{product_id}/edit", response_class=HTMLResponse)
def admin_product_edit(request: Request, product_id: int, auto_generate: bool = False):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    with db() as conn:
        product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            raise HTTPException(status_code=404)
        images = conn.execute(
            "SELECT * FROM product_images WHERE product_id = ? ORDER BY sort_order",
            (product_id,)
        ).fetchall()
    return templates.TemplateResponse("admin/product_form.html", {
        "request": request, "product": product, "images": images,
        "has_ai": bool(ANTHROPIC_API_KEY), "auto_generate": auto_generate,
        "csrf_token": get_csrf_token(request)
    })


@app.post("/admin/products/create")
def admin_product_create(
    request: Request,
    csrf_token: str = Form(""),
    name: str = Form(...),
    short_description: str = Form(""),
    description: str = Form(""),
    price: float = Form(0),
    stock_status: str = Form("instock"),
    category: str = Form(""),
    video_url: str = Form(""),
):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    validate_csrf(request, csrf_token)
    slug = slugify(name)
    with db() as conn:
        cursor = conn.execute(
            """INSERT INTO products (name, slug, description, short_description, price, stock_status, category, video_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, slug, description, short_description, price, stock_status, category, video_url)
        )
        conn.commit()
        new_id = cursor.lastrowid
    return RedirectResponse(f"/admin/products/{new_id}/edit", status_code=302)


@app.post("/admin/products/{product_id}/edit")
def admin_product_update(
    request: Request,
    product_id: int,
    csrf_token: str = Form(""),
    name: str = Form(...),
    short_description: str = Form(""),
    description: str = Form(""),
    price: float = Form(0),
    stock_status: str = Form("instock"),
    category: str = Form(""),
    video_url: str = Form(""),
):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    validate_csrf(request, csrf_token)
    slug = slugify(name)
    with db() as conn:
        conn.execute(
            """UPDATE products SET name=?, slug=?, description=?, short_description=?,
               price=?, stock_status=?, category=?, video_url=? WHERE id=?""",
            (name, slug, description, short_description, price, stock_status, category, video_url, product_id)
        )
        conn.commit()
    return RedirectResponse("/admin/products", status_code=302)


@app.post("/admin/products/{product_id}/delete")
def admin_product_delete(request: Request, product_id: int, csrf_token: str = Form("")):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    validate_csrf(request, csrf_token)
    with db() as conn:
        # Delete images from disk
        images = conn.execute(
            "SELECT image_path FROM product_images WHERE product_id = ?", (product_id,)
        ).fetchall()
        for img in images:
            path = os.path.join(UPLOAD_DIR, img["image_path"])
            if os.path.exists(path):
                os.remove(path)
        conn.execute("DELETE FROM product_images WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
    return RedirectResponse("/admin/products", status_code=302)


@app.post("/admin/products/{product_id}/upload")
async def admin_product_upload(request: Request, product_id: int, csrf_token: str = Form(""), image: UploadFile = File(...)):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    validate_csrf(request, csrf_token)

    # Determine file extension from filename or content type
    ext = os.path.splitext(image.filename or "")[1].lower()
    content_type = (image.content_type or "").lower()

    # Known good extensions
    allowed_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}

    # Map content types to extensions (fallback when extension is missing/unknown)
    type_to_ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/heic": ".jpg",
        "image/heif": ".jpg",
    }

    if ext in allowed_ext:
        # HEIC/HEIF: save with .jpg extension
        if ext in (".heic", ".heif"):
            ext = ".jpg"
    elif content_type in type_to_ext:
        ext = type_to_ext[content_type]
    elif content_type.startswith("image/"):
        # Accept any image/* content type, save as .jpg
        ext = ".jpg"
    else:
        raise HTTPException(status_code=400, detail=f"Invalid image type: {ext} ({content_type})")

    filename = f"{product_id}_{secrets.token_hex(8)}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        content = await image.read()
        f.write(content)

    with db() as conn:
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) FROM product_images WHERE product_id = ?",
            (product_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO product_images (product_id, image_path, sort_order) VALUES (?, ?, ?)",
            (product_id, filename, max_order + 1)
        )
        conn.commit()

    # Redirect back to edit with auto_generate flag so AI kicks off automatically
    return RedirectResponse(f"/admin/products/{product_id}/edit?auto_generate=1", status_code=302)


@app.post("/admin/products/images/{image_id}/delete")
def admin_image_delete(request: Request, image_id: int, csrf_token: str = Form("")):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    validate_csrf(request, csrf_token)
    with db() as conn:
        img = conn.execute("SELECT * FROM product_images WHERE id = ?", (image_id,)).fetchone()
        if img:
            path = os.path.join(UPLOAD_DIR, img["image_path"])
            if os.path.exists(path):
                os.remove(path)
            product_id = img["product_id"]
            conn.execute("DELETE FROM product_images WHERE id = ?", (image_id,))
            conn.commit()
            return RedirectResponse(f"/admin/products/{product_id}/edit", status_code=302)
    return RedirectResponse("/admin/products", status_code=302)


# --- AI Description Generation ---

@app.post("/admin/products/{product_id}/generate")
async def admin_generate_description(request: Request, product_id: int):
    if not is_admin(request):
        return JSONResponse({"error": "Not authorized"}, status_code=401)

    # CSRF validation via header for fetch() requests
    _csrf = request.headers.get("X-CSRF-Token", "")
    _session_csrf = request.session.get("csrf_token", "")
    if not _csrf or not _session_csrf or not secrets.compare_digest(_session_csrf, _csrf):
        return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

    if not ANTHROPIC_API_KEY:
        return JSONResponse({"error": "AI not configured. Add ANTHROPIC_API_KEY to .env"}, status_code=500)

    with db() as conn:
        product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            return JSONResponse({"error": "Product not found"}, status_code=404)
        images = conn.execute(
            "SELECT image_path FROM product_images WHERE product_id = ? ORDER BY sort_order",
            (product_id,)
        ).fetchall()

    if not images:
        return JSONResponse({"error": "Upload at least one image first"}, status_code=400)

    # Build image content blocks for Claude (up to 3 images)
    image_blocks = []
    for img in images[:3]:
        filepath = os.path.join(UPLOAD_DIR, img["image_path"])
        if not os.path.exists(filepath):
            continue
        ext = os.path.splitext(filepath)[1].lower()
        media_type = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"
        }.get(ext, "image/jpeg")
        with open(filepath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        image_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64}
        })

    if not image_blocks:
        return JSONResponse({"error": "Could not read product images"}, status_code=500)

    product_name = product["name"]
    product_category = product["category"] or "Unknown"

    prompt_text = f"""You are writing a product description for a live edge wood slab sold by Hardwood Haven of Idaho. The product is called "{product_name}" and the species/category is "{product_category}".

Look at the photo(s) of this slab carefully. Based on what you see, write:

1. A SHORT DESCRIPTION (1 sentence) — a compelling one-liner that highlights what makes this slab special. Keep it casual and knowledgeable, like a wood seller talking to a fellow woodworker.

2. A FULL DESCRIPTION in HTML with:
   - Two paragraphs about the slab. First paragraph: describe the visual character, grain, and what makes it unique. Second paragraph: suggest what it would be great for (table, mantel, desk, bar top, etc.) based on its size and character. Write in a warm, knowledgeable tone — not salesy, but passionate about wood.
   - An HTML specs table with these rows: Wood Species, Dimensions (estimate from the photo if you can see a reference, otherwise write "Contact for exact measurements"), Finish (typically "Sanded one side, unfinished natural surface" unless the photo shows otherwise), and Character (describe notable features like grain pattern, live edge, knots, spalting, figure, etc.)
   - A CTA box at the bottom

Use this exact HTML format for the specs table and CTA:

<table style="width:100%;border-collapse:collapse;margin:15px 0">
<tr style="border-bottom:1px solid #ddd"><td style="padding:8px;font-weight:bold;width:35%">Wood Species</td><td style="padding:8px">SPECIES HERE</td></tr>
<tr style="border-bottom:1px solid #ddd;background:#fafafa"><td style="padding:8px;font-weight:bold">Dimensions</td><td style="padding:8px">DIMS HERE</td></tr>
<tr style="border-bottom:1px solid #ddd"><td style="padding:8px;font-weight:bold">Finish</td><td style="padding:8px">FINISH HERE</td></tr>
<tr style="border-bottom:1px solid #ddd;background:#fafafa"><td style="padding:8px;font-weight:bold">Character</td><td style="padding:8px">CHARACTER HERE</td></tr>
</table>
<div style="margin-top:20px;padding:15px;background:#f9f6f1;border-left:4px solid #8B4513">
<strong>This is a one-of-a-kind slab.</strong> Once it's gone, it's gone.<br>
Have questions or want to see more photos? Call Alan at <strong>(208) 680-2616</strong> or <a href="/quote">request a quote</a>.
</div>

Return your response as JSON with exactly two keys:
{{"short_description": "the one-liner", "description": "the full HTML description"}}

Return ONLY the JSON, no markdown formatting or code blocks."""

    content_blocks = image_blocks + [{"type": "text", "text": prompt_text}]

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            messages=[{"role": "user", "content": content_blocks}]
        )

        raw = response.content[0].text.strip()
        # Handle potential markdown code block wrapping
        if raw.startswith("```"):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

        import json
        result = json.loads(raw)
        return JSONResponse(result)

    except Exception as e:
        return JSONResponse({"error": f"AI generation failed: {str(e)}"}, status_code=500)

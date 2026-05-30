from flask import Flask, request, jsonify, send_file, make_response
from flask_cors import CORS
import os, uuid, zipfile, tempfile, shutil
from werkzeug.utils import secure_filename
from pypdf import PdfReader, PdfWriter
from pdf2docx import Converter
from PIL import Image
import io

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = tempfile.mkdtemp()
OUTPUT_FOLDER = tempfile.mkdtemp()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

def allowed_file(filename, types):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in types

def make_output_path(ext):
    return os.path.join(OUTPUT_FOLDER, f"{uuid.uuid4().hex}.{ext}")

def cleanup(*paths):
    for p in paths:
        try:
            if os.path.isfile(p): os.remove(p)
        except: pass

def cors_response(data, status=200):
    response = make_response(jsonify(data), status)
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

@app.route('/', methods=['GET'])
def index():
    return jsonify({"status": "EggyPDF API is running!", "tools": 8})


@app.route('/api/merge', methods=['POST', 'OPTIONS'])
def merge_pdf():
    if request.method == 'OPTIONS':
        return cors_response({})
    try:
        files = request.files.getlist('files')
        if len(files) < 2:
            return cors_response({"error": "Please upload at least 2 PDF files."}, 400)
        saved = []
        for f in files:
            path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
            f.save(path)
            saved.append(path)
        out = make_output_path('pdf')
        writer = PdfWriter()
        for p in saved:
            reader = PdfReader(p)
            for page in reader.pages:
                writer.add_page(page)
        with open(out, 'wb') as fh:
            writer.write(fh)
        cleanup(*saved)
        response = make_response(send_file(out, as_attachment=True, download_name='merged.pdf', mimetype='application/pdf'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        return cors_response({"error": str(e)}, 500)


@app.route('/api/split', methods=['POST', 'OPTIONS'])
def split_pdf():
    if request.method == 'OPTIONS':
        return cors_response({})
    try:
        f = request.files.get('file')
        if not f:
            return cors_response({"error": "Please upload a PDF file."}, 400)
        split_type = request.form.get('type', 'all')
        page_range = request.form.get('range', '')
        saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
        f.save(saved)
        reader = PdfReader(saved)
        total = len(reader.pages)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            if split_type == 'all':
                for i in range(total):
                    writer = PdfWriter()
                    writer.add_page(reader.pages[i])
                    buf = io.BytesIO()
                    writer.write(buf)
                    zf.writestr(f"page_{i+1}.pdf", buf.getvalue())
            else:
                pages = set()
                for part in page_range.split(','):
                    part = part.strip()
                    if '-' in part:
                        a, b = part.split('-')
                        pages.update(range(int(a)-1, int(b)))
                    elif part.isdigit():
                        pages.add(int(part)-1)
                writer = PdfWriter()
                for idx in sorted(pages):
                    if 0 <= idx < total:
                        writer.add_page(reader.pages[idx])
                buf = io.BytesIO()
                writer.write(buf)
                zf.writestr("split_pages.pdf", buf.getvalue())
        cleanup(saved)
        zip_buf.seek(0)
        response = make_response(send_file(zip_buf, as_attachment=True, download_name='split_pages.zip', mimetype='application/zip'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        return cors_response({"error": str(e)}, 500)


@app.route('/api/compress', methods=['POST', 'OPTIONS'])
def compress_pdf():
    if request.method == 'OPTIONS':
        return cors_response({})
    try:
        f = request.files.get('file')
        if not f:
            return cors_response({"error": "Please upload a PDF file."}, 400)

        level = request.form.get('level', 'medium')
        # Quality settings per level
        quality_map = {'low': 85, 'medium': 60, 'high': 35}
        img_quality = quality_map.get(level, 60)

        saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
        f.save(saved)
        out = make_output_path('pdf')

        reader = PdfReader(saved)
        writer = PdfWriter()

        for page in reader.pages:
            # Compress images on each page
            if '/Resources' in page:
                resources = page['/Resources']
                if '/XObject' in resources:
                    xobjects = resources['/XObject'].get_object()
                    for obj_name in xobjects:
                        xobj = xobjects[obj_name].get_object()
                        if xobj.get('/Subtype') == '/Image':
                            try:
                                # Get image data and recompress
                                img_data = xobj.get_data()
                                img = Image.open(io.BytesIO(img_data))
                                # Convert to RGB if needed
                                if img.mode in ('RGBA', 'LA', 'P'):
                                    img = img.convert('RGB')
                                # Resize if very large
                                max_size = {'low': 1200, 'medium': 800, 'high': 600}
                                max_dim = max_size.get(level, 800)
                                if max(img.size) > max_dim:
                                    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                                # Compress to JPEG
                                img_buf = io.BytesIO()
                                img.save(img_buf, format='JPEG', quality=img_quality, optimize=True)
                                img_buf.seek(0)
                                # Replace image data
                                xobj.clear()
                                xobj.set_data(img_buf.read())
                                xobj['/Filter'] = '/DCTDecode'
                                xobj['/ColorSpace'] = '/DeviceRGB'
                                xobj['/BitsPerComponent'] = 8
                            except Exception:
                                pass  # Skip images that can't be compressed

            # Compress content streams
            page.compress_content_streams()
            writer.add_page(page)

        # Write compressed PDF
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
        with open(out, 'wb') as fh:
            writer.write(fh)

        cleanup(saved)

        # Check if compression actually reduced size
        orig_size = os.path.getsize(saved) if os.path.exists(saved) else 0
        new_size = os.path.getsize(out)

        response = make_response(send_file(out, as_attachment=True, download_name='compressed.pdf', mimetype='application/pdf'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    except Exception as e:
        return cors_response({"error": f"Compression failed: {str(e)}"}), 500


@app.route('/api/pdf-to-word', methods=['POST', 'OPTIONS'])
def pdf_to_word():
    if request.method == 'OPTIONS':
        return cors_response({})
    try:
        f = request.files.get('file')
        if not f:
            return cors_response({"error": "Please upload a PDF file."}, 400)
        saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
        f.save(saved)
        out = make_output_path('docx')
        cv = Converter(saved)
        cv.convert(out, start=0, end=None)
        cv.close()
        cleanup(saved)
        response = make_response(send_file(out, as_attachment=True, download_name='converted.docx',
                    mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        return cors_response({"error": str(e)}, 500)


@app.route('/api/jpg-to-pdf', methods=['POST', 'OPTIONS'])
def jpg_to_pdf():
    if request.method == 'OPTIONS':
        return cors_response({})
    try:
        files = request.files.getlist('files')
        if not files:
            return cors_response({"error": "Please upload at least one image."}, 400)
        images = []
        saved_paths = []
        for f in files:
            path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}_{secure_filename(f.filename)}")
            f.save(path)
            saved_paths.append(path)
            img = Image.open(path).convert('RGB')
            images.append(img)
        out = make_output_path('pdf')
        if len(images) == 1:
            images[0].save(out, 'PDF', resolution=100.0)
        else:
            images[0].save(out, 'PDF', resolution=100.0, save_all=True, append_images=images[1:])
        cleanup(*saved_paths)
        response = make_response(send_file(out, as_attachment=True, download_name='images.pdf', mimetype='application/pdf'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        return cors_response({"error": str(e)}, 500)


@app.route('/api/watermark', methods=['POST', 'OPTIONS'])
def add_watermark():
    if request.method == 'OPTIONS':
        return cors_response({})
    try:
        f = request.files.get('file')
        text = request.form.get('text', 'CONFIDENTIAL')
        if not f:
            return cors_response({"error": "Please upload a PDF file."}, 400)
        saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
        f.save(saved)
        wm_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}_wm.pdf")
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.pagesizes import A4
        c = rl_canvas.Canvas(wm_path, pagesize=A4)
        c.setFont("Helvetica-Bold", 48)
        c.setFillColorRGB(0.7, 0.7, 0.7, alpha=0.35)
        c.saveState()
        c.translate(A4[0]/2, A4[1]/2)
        c.rotate(45)
        c.drawCentredString(0, 0, text.upper())
        c.restoreState()
        c.save()
        reader = PdfReader(saved)
        wm_reader = PdfReader(wm_path)
        wm_page = wm_reader.pages[0]
        writer = PdfWriter()
        for page in reader.pages:
            page.merge_page(wm_page)
            writer.add_page(page)
        out = make_output_path('pdf')
        with open(out, 'wb') as fh:
            writer.write(fh)
        cleanup(saved, wm_path)
        response = make_response(send_file(out, as_attachment=True, download_name='watermarked.pdf', mimetype='application/pdf'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        return cors_response({"error": str(e)}, 500)


@app.route('/api/protect', methods=['POST', 'OPTIONS'])
def protect_pdf():
    if request.method == 'OPTIONS':
        return cors_response({})
    try:
        f = request.files.get('file')
        password = request.form.get('password', '')
        if not f:
            return cors_response({"error": "Please upload a PDF file."}, 400)
        if not password or len(password) < 4:
            return cors_response({"error": "Password must be at least 4 characters."}, 400)
        saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
        f.save(saved)
        reader = PdfReader(saved)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(password)
        out = make_output_path('pdf')
        with open(out, 'wb') as fh:
            writer.write(fh)
        cleanup(saved)
        response = make_response(send_file(out, as_attachment=True, download_name='protected.pdf', mimetype='application/pdf'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        return cors_response({"error": str(e)}, 500)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

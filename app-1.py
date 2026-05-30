from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os, uuid, zipfile, tempfile, shutil
from werkzeug.utils import secure_filename
from pypdf import PdfReader, PdfWriter
from pdf2docx import Converter
from PIL import Image
import subprocess
import io

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

UPLOAD_FOLDER = tempfile.mkdtemp()
OUTPUT_FOLDER = tempfile.mkdtemp()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

ALLOWED = {'pdf', 'doc', 'docx', 'jpg', 'jpeg', 'png'}

def allowed_file(filename, types=None):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in (types or ALLOWED)

def make_output_path(ext):
    return os.path.join(OUTPUT_FOLDER, f"{uuid.uuid4().hex}.{ext}")

def cleanup(*paths):
    for p in paths:
        try:
            if os.path.isfile(p): os.remove(p)
            elif os.path.isdir(p): shutil.rmtree(p)
        except: pass

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response


@app.route('/')
def index():
    return jsonify({"status": "EggyPDF API is running!", "tools": 8})


@app.route('/api/merge', methods=['POST', 'OPTIONS'])
def merge_pdf():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    files = request.files.getlist('files')
    if len(files) < 2:
        return jsonify({"error": "Please upload at least 2 PDF files."}), 400
    saved = []
    for f in files:
        if not allowed_file(f.filename, {'pdf'}):
            return jsonify({"error": f"{f.filename} is not a valid PDF."}), 400
        path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
        f.save(path)
        saved.append(path)
    out = make_output_path('pdf')
    writer = PdfWriter()
    for p in saved:
        reader = PdfReader(p)
        for page in reader.pages:
            writer.add_page(page)
    with open(out, 'wb') as f:
        writer.write(f)
    cleanup(*saved)
    return send_file(out, as_attachment=True, download_name='merged.pdf', mimetype='application/pdf')


@app.route('/api/split', methods=['POST', 'OPTIONS'])
def split_pdf():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    f = request.files.get('file')
    if not f or not allowed_file(f.filename, {'pdf'}):
        return jsonify({"error": "Please upload a valid PDF file."}), 400
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
    return send_file(zip_buf, as_attachment=True, download_name='split_pages.zip', mimetype='application/zip')


@app.route('/api/compress', methods=['POST', 'OPTIONS'])
def compress_pdf():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    f = request.files.get('file')
    if not f or not allowed_file(f.filename, {'pdf'}):
        return jsonify({"error": "Please upload a valid PDF file."}), 400

    saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
    f.save(saved)
    out = make_output_path('pdf')

    try:
        # Pure Python compression using pypdf
        reader = PdfReader(saved)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        # Compress each page
        for page in writer.pages:
            page.compress_content_streams()
        with open(out, 'wb') as fh:
            writer.write(fh)
        cleanup(saved)
        return send_file(out, as_attachment=True, download_name='compressed.pdf', mimetype='application/pdf')
    except Exception as e:
        cleanup(saved)
        return jsonify({"error": f"Compression failed: {str(e)}"}), 500


@app.route('/api/pdf-to-word', methods=['POST', 'OPTIONS'])
def pdf_to_word():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    f = request.files.get('file')
    if not f or not allowed_file(f.filename, {'pdf'}):
        return jsonify({"error": "Please upload a valid PDF file."}), 400
    saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
    f.save(saved)
    out = make_output_path('docx')
    try:
        cv = Converter(saved)
        cv.convert(out, start=0, end=None)
        cv.close()
        cleanup(saved)
        return send_file(out, as_attachment=True, download_name='converted.docx',
                        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    except Exception as e:
        cleanup(saved)
        return jsonify({"error": f"Conversion failed: {str(e)}"}), 500


@app.route('/api/word-to-pdf', methods=['POST', 'OPTIONS'])
def word_to_pdf():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    f = request.files.get('file')
    if not f or not allowed_file(f.filename, {'doc', 'docx'}):
        return jsonify({"error": "Please upload a valid Word file."}), 400
    saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.docx")
    f.save(saved)
    out = make_output_path('pdf')
    try:
        # Try LibreOffice first
        result = subprocess.run([
            'libreoffice', '--headless', '--convert-to', 'pdf',
            '--outdir', OUTPUT_FOLDER, saved
        ], capture_output=True, timeout=60)
        expected = os.path.join(OUTPUT_FOLDER, os.path.splitext(os.path.basename(saved))[0] + '.pdf')
        if os.path.exists(expected):
            shutil.move(expected, out)
        # Try soffice if libreoffice didn't work
        if not os.path.exists(out):
            result = subprocess.run([
                'soffice', '--headless', '--convert-to', 'pdf',
                '--outdir', OUTPUT_FOLDER, saved
            ], capture_output=True, timeout=60)
            expected = os.path.join(OUTPUT_FOLDER, os.path.splitext(os.path.basename(saved))[0] + '.pdf')
            if os.path.exists(expected):
                shutil.move(expected, out)
        cleanup(saved)
        if not os.path.exists(out):
            return jsonify({"error": "Conversion failed. LibreOffice not available on server."}), 500
        return send_file(out, as_attachment=True, download_name='converted.pdf', mimetype='application/pdf')
    except Exception as e:
        cleanup(saved)
        return jsonify({"error": f"Conversion failed: {str(e)}"}), 500


@app.route('/api/jpg-to-pdf', methods=['POST', 'OPTIONS'])
def jpg_to_pdf():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "Please upload at least one image."}), 400
    images = []
    saved_paths = []
    try:
        for f in files:
            if not allowed_file(f.filename, {'jpg', 'jpeg', 'png'}):
                return jsonify({"error": f"{f.filename} is not a valid image."}), 400
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
        return send_file(out, as_attachment=True, download_name='images.pdf', mimetype='application/pdf')
    except Exception as e:
        cleanup(*saved_paths)
        return jsonify({"error": f"Conversion failed: {str(e)}"}), 500


@app.route('/api/watermark', methods=['POST', 'OPTIONS'])
def add_watermark():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    f = request.files.get('file')
    text = request.form.get('text', 'CONFIDENTIAL')
    if not f or not allowed_file(f.filename, {'pdf'}):
        return jsonify({"error": "Please upload a valid PDF file."}), 400
    saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
    f.save(saved)
    wm_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}_wm.pdf")
    try:
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
        return send_file(out, as_attachment=True, download_name='watermarked.pdf', mimetype='application/pdf')
    except Exception as e:
        cleanup(saved, wm_path)
        return jsonify({"error": f"Watermark failed: {str(e)}"}), 500


@app.route('/api/protect', methods=['POST', 'OPTIONS'])
def protect_pdf():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    f = request.files.get('file')
    password = request.form.get('password', '')
    if not f or not allowed_file(f.filename, {'pdf'}):
        return jsonify({"error": "Please upload a valid PDF file."}), 400
    if not password or len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters."}), 400
    saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
    f.save(saved)
    try:
        reader = PdfReader(saved)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(password)
        out = make_output_path('pdf')
        with open(out, 'wb') as fh:
            writer.write(fh)
        cleanup(saved)
        return send_file(out, as_attachment=True, download_name='protected.pdf', mimetype='application/pdf')
    except Exception as e:
        cleanup(saved)
        return jsonify({"error": f"Protection failed: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

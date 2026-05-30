from flask import Flask, request, jsonify, send_file, make_response
from flask_cors import CORS
import os, uuid, zipfile, tempfile, shutil
from werkzeug.utils import secure_filename
from pypdf import PdfReader, PdfWriter
from pdf2docx import Converter
from PIL import Image
import pikepdf
import io

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = tempfile.mkdtemp()
OUTPUT_FOLDER = tempfile.mkdtemp()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

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
        quality_map = {'low': 80, 'medium': 50, 'high': 25}
        img_quality = quality_map.get(level, 50)
        size_map = {'low': 1400, 'medium': 900, 'high': 600}
        max_dim = size_map.get(level, 900)

        saved = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
        f.save(saved)
        out = make_output_path('pdf')

        # Use pikepdf for real compression
        with pikepdf.open(saved) as pdf:
            for page in pdf.pages:
                for key, obj in page.images.items():
                    try:
                        # Read image
                        pdfimage = pikepdf.PdfImage(obj)
                        pil_img = pdfimage.as_pil_image()

                        # Convert to RGB
                        if pil_img.mode in ('RGBA', 'LA', 'P', 'L'):
                            pil_img = pil_img.convert('RGB')

                        # Resize if too large
                        w, h = pil_img.size
                        if max(w, h) > max_dim:
                            pil_img.thumbnail((max_dim, max_dim), Image.LANCZOS)

                        # Compress to JPEG
                        buf = io.BytesIO()
                        pil_img.save(buf, format='JPEG', quality=img_quality, optimize=True)
                        buf.seek(0)

                        # Replace image in PDF
                        obj.write(buf.read(), filter=pikepdf.Name('/DCTDecode'))
                        obj['/ColorSpace'] = pikepdf.Name('/DeviceRGB')
                        obj['/BitsPerComponent'] = 8
                        new_w, new_h = pil_img.size
                        obj['/Width'] = new_w
                        obj['/Height'] = new_h

                    except Exception:
                        pass  # Skip uncompressible images

            # Save with maximum compression
            pdf.save(out, compress_streams=True, recompress_flate=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate)

        cleanup(saved)
        response = make_response(send_file(out, as_attachment=True, download_name='compressed.pdf', mimetype='application/pdf'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    except Exception as e:
        cleanup(saved if 'saved' in locals() else '')
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

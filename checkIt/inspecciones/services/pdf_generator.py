import io
import os
from datetime import datetime

from django.utils.timezone import localtime, now as tz_now
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Paleta ──────────────────────────────────────────────────────────────────
PRIMARY = colors.HexColor('#1a3a5c')
ACCENT = colors.HexColor('#e8f0fe')
SUCCESS = colors.HexColor('#2e7d32')
DANGER = colors.HexColor('#c62828')
LIGHT_GREY = colors.HexColor('#f5f5f5')
MID_GREY = colors.HexColor('#bdbdbd')
TEXT = colors.HexColor('#212121')

PAGE_W, PAGE_H = A4
MARGIN = 2 * cm


def _styles():
    """Centraliza los estilos tipográficos usados a lo largo del informe PDF."""
    base = getSampleStyleSheet()
    return {
        'h1': ParagraphStyle('h1', parent=base['Heading1'],
                             textColor=PRIMARY, fontSize=18, spaceAfter=4),
        'h2': ParagraphStyle('h2', parent=base['Heading2'],
                             textColor=PRIMARY, fontSize=12, spaceBefore=14, spaceAfter=4),
        'label': ParagraphStyle('label', parent=base['Normal'],
                                textColor=colors.HexColor('#616161'), fontSize=8),
        'value': ParagraphStyle('value', parent=base['Normal'],
                                textColor=TEXT, fontSize=10),
        'small': ParagraphStyle('small', parent=base['Normal'],
                                fontSize=7, textColor=colors.HexColor('#757575')),
        'legal': ParagraphStyle('legal', parent=base['Normal'],
                                fontSize=7, textColor=colors.HexColor('#9e9e9e'),
                                leading=10),
        'integrity_ok': ParagraphStyle('integrity_ok', parent=base['Normal'],
                                       textColor=SUCCESS, fontSize=8, alignment=1),
        'integrity_fail': ParagraphStyle('integrity_fail', parent=base['Normal'],
                                         textColor=DANGER, fontSize=8, alignment=1),
    }


def _kv_table(rows, col_widths=None):
    """Tabla de dos columnas clave / valor."""
    col_widths = col_widths or [4 * cm, PAGE_W - 2 * MARGIN - 4 * cm]
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), ACCENT),
        ('TEXTCOLOR', (0, 0), (0, -1), PRIMARY),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, LIGHT_GREY]),
        ('GRID', (0, 0), (-1, -1), 0.25, MID_GREY),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    return t


def _evidence_thumbnail(evidence, max_side=2.5 * cm):
    """Devuelve un Image reportlab si el archivo es una imagen, o un placeholder de texto."""
    try:
        path = evidence.file.path
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'):
            return Image(path, width=max_side, height=max_side, kind='bound')
    except Exception:  # nosec B110 — fallback intencional: archivo puede ser vídeo o estar corrupto
        pass
    return Paragraph('[sin miniatura]', getSampleStyleSheet()['Normal'])


def generate_claim_pdf(inspection) -> bytes:
    """
    Construye el informe completo de reclamación a partir de una inspección y lo devuelve en memoria.
    """
    s = _styles()
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=f'Reclamación #{inspection.pk}',
        author=inspection.property.organization.name,
    )

    story = []

    # ── 1. Cabecera ──────────────────────────────────────────────────────────
    org_name = inspection.property.organization.name
    report_id = f'RPT-{inspection.pk:06d}'
    issued_at = localtime(tz_now()).strftime('%d/%m/%Y %H:%M')

    story.append(Paragraph(f'INFORME DE RECLAMACIÓN DE DAÑOS', s['h1']))
    story.append(Paragraph(f'{org_name}  ·  {report_id}  ·  Emitido: {issued_at}', s['label']))
    story.append(HRFlowable(width='100%', thickness=2, color=PRIMARY, spaceAfter=10))

    # ── 2. Propiedad ─────────────────────────────────────────────────────────
    story.append(Paragraph('Propiedad', s['h2']))
    prop = inspection.property
    story.append(_kv_table([
        ['Nombre', prop.name],
        ['Dirección', prop.address],
        ['Código de referencia', prop.reference_code],
    ]))
    story.append(Spacer(1, 0.4 * cm))

    # ── 3. Reserva / Huésped ─────────────────────────────────────────────────
    booking = inspection.booking
    if booking:
        story.append(Paragraph('Reserva', s['h2']))
        story.append(_kv_table([
            ['Huésped', booking.guest_name],
            ['Email', booking.guest_email or '—'],
            ['Plataforma', booking.get_platform_display()],
            ['ID reserva', booking.platform_booking_id or '—'],
            ['Check-in', booking.check_in.strftime('%d/%m/%Y')],
            ['Check-out', booking.check_out.strftime('%d/%m/%Y')],
        ]))
        story.append(Spacer(1, 0.4 * cm))

    # ── 4. Inspección ────────────────────────────────────────────────────────
    story.append(Paragraph('Inspección', s['h2']))
    operator_name = inspection.operator.get_full_name() if inspection.operator else '—'
    created_str = localtime(inspection.created_at).strftime('%d/%m/%Y %H:%M')
    rows = [
        ['ID inspección', f'#{inspection.pk}'],
        ['Estado', inspection.get_status_display()],
        ['Operario', operator_name],
        ['Fecha de apertura', created_str],
    ]
    if inspection.notes:
        rows.append(['Notas', inspection.notes])
    story.append(_kv_table(rows))
    story.append(Spacer(1, 0.4 * cm))

    # ── 5. Evidencias ────────────────────────────────────────────────────────
    evidences = list(inspection.evidences.all().order_by('room', 'uploaded_at'))
    story.append(Paragraph(f'Evidencias ({len(evidences)})', s['h2']))

    if not evidences:
        story.append(Paragraph('No se registraron evidencias en esta inspección.', s['value']))
    else:
        # Cabecera de la tabla
        ev_header = [
            Paragraph('<b>Archivo</b>', s['small']),
            Paragraph('<b>Sala</b>', s['small']),
            Paragraph('<b>Descripción</b>', s['small']),
            Paragraph('<b>Capturada</b>', s['small']),
            Paragraph('<b>GPS</b>', s['small']),
            Paragraph('<b>Integridad</b>', s['small']),
        ]
        ev_rows = [ev_header]

        for ev in evidences:
            integrity_ok = bool(ev.sha256_server) and ev.sha256_client == ev.sha256_server
            integrity_style = s['integrity_ok'] if integrity_ok else s['integrity_fail']
            integrity_text = '✓ OK' if integrity_ok else '✗ FALLO'

            gps = '—'
            if ev.latitude and ev.longitude:
                gps = f'{ev.latitude:.5f}\n{ev.longitude:.5f}'

            captured = localtime(ev.captured_at).strftime('%d/%m/%Y\n%H:%M:%S')

            ev_rows.append([
                _evidence_thumbnail(ev),
                Paragraph(ev.get_room_display(), s['small']),
                Paragraph(ev.description or '—', s['small']),
                Paragraph(captured, s['small']),
                Paragraph(gps, s['small']),
                Paragraph(integrity_text, integrity_style),
            ])

        col_w = [2.8 * cm, 2.2 * cm, 5.5 * cm, 2.5 * cm, 2.5 * cm, 1.8 * cm]
        ev_table = Table(ev_rows, colWidths=col_w, repeatRows=1)
        ev_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
            ('GRID', (0, 0), (-1, -1), 0.25, MID_GREY),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(ev_table)

    # ── 6. Pie legal ─────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.8 * cm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=MID_GREY, spaceAfter=6))
    story.append(Paragraph(
        'Este documento ha sido generado automáticamente por la plataforma CheckIt. '
        'Las evidencias contenidas en este informe disponen de hash SHA-256 calculado '
        'en el dispositivo de captura y verificado en servidor, garantizando su integridad '
        'e inmutabilidad conforme a los estándares de cadena de custodia digital. '
        'Documento válido para su presentación ante plataformas de alquiler vacacional '
        'y compañías aseguradoras.',
        s['legal'],
    ))

    doc.build(story)
    return buffer.getvalue()


def generar_informe_base(inspeccion):
    """Genera el PDF base de la reclamación usando los campos reales del modelo."""
    os.makedirs('tmp', exist_ok=True)
    output_path = f"tmp/base_reclamacion_{inspeccion.id}.pdf"

    s = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f'Reclamación #{inspeccion.pk}',
    )
    story = []

    story.append(Paragraph('INFORME DE RECLAMACIÓN DE DAÑOS', s['h1']))
    story.append(Paragraph(f'CheckIt  ·  RPT-{inspeccion.pk:06d}', s['label']))
    story.append(HRFlowable(width='100%', thickness=2, color=PRIMARY, spaceAfter=10))

    story.append(Paragraph('Propiedad', s['h2']))
    story.append(_kv_table([
        ['Nombre', inspeccion.propiedad.nombre],
        ['Dirección', inspeccion.propiedad.direccion],
        ['Descripción', inspeccion.propiedad.descripcion or '—'],
    ]))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph('Inspección', s['h2']))
    operario = inspeccion.operario.get_full_name() or inspeccion.operario.username if inspeccion.operario else '—'
    story.append(_kv_table([
        ['ID', f'#{inspeccion.pk}'],
        ['Estado', inspeccion.get_estado_display()],
        ['Operario', operario],
        ['Fecha', inspeccion.fecha_creacion.strftime('%d/%m/%Y %H:%M')],
    ]))
    story.append(Spacer(1, 0.4 * cm))

    evidencias = list(inspeccion.evidencias.all())
    story.append(Paragraph(f'Evidencias ({len(evidencias)})', s['h2']))
    if not evidencias:
        story.append(Paragraph('No se registraron evidencias en esta inspección.', s['value']))
    else:
        for ev in evidencias:
            story.append(Paragraph(f'• {ev.descripcion or "Sin descripción"} — {ev.fecha_captura.strftime("%d/%m/%Y %H:%M")}', s['value']))

    # ── Integridad Criptográfica y Sello de Tiempo ───────────────────────────
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph('Integridad Criptográfica y Sello de Tiempo (RFC 3161)', s['h2']))

    ev_con_hash = [ev for ev in evidencias if ev.hash_sha256 or ev.tsa_timestamp]
    if ev_con_hash:
        tsa_header = [
            Paragraph('<b>#</b>', s['small']),
            Paragraph('<b>Hash SHA-256</b>', s['small']),
            Paragraph('<b>Sello TSA (RFC 3161)</b>', s['small']),
        ]
        tsa_rows = [tsa_header]
        for ev in ev_con_hash:
            ts_str = ev.tsa_timestamp.strftime('%d/%m/%Y %H:%M:%S UTC') if ev.tsa_timestamp else '—'
            tsa_rows.append([
                Paragraph(str(ev.id), s['small']),
                Paragraph(ev.hash_sha256 or '—', s['small']),
                Paragraph(ts_str, s['small']),
            ])
        available_w = PAGE_W - 2 * MARGIN
        tsa_table = Table(tsa_rows, colWidths=[1.5 * cm, available_w - 7 * cm, 5.5 * cm])
        tsa_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
            ('GRID', (0, 0), (-1, -1), 0.25, MID_GREY),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(tsa_table)
    else:
        story.append(Paragraph(
            'No se dispone de sellos de tiempo TSA para las evidencias de esta inspección.',
            s['value'],
        ))

    story.append(Spacer(1, 0.3 * cm))

    # ── Firma Digital X.509 ──────────────────────────────────────────────────
    story.append(Paragraph('Firma Digital X.509 / PKCS#7', s['h2']))
    cert_path = os.getenv('PDF_SIGNING_CERT_PATH', 'certs/signing.crt')
    firma_rows = [
        ['Tipo de firma', 'X.509 / PKCS#7 (PDF con escritura incremental)'],
        ['Algoritmo', 'RSA + SHA-256'],
        ['Campo de firma', 'Firma_Pericial_CheckIt'],
        ['Certificado', os.path.basename(cert_path)],
        ['Fecha de emisión', localtime(tz_now()).strftime('%d/%m/%Y %H:%M:%S')],
    ]
    story.append(_kv_table(firma_rows, col_widths=[4.5 * cm, PAGE_W - 2 * MARGIN - 4.5 * cm]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        'La firma digital X.509 integrada en este PDF garantiza su inmutabilidad legal desde el momento '
        'de la firma. Para verificarla, ábralo con Adobe Acrobat Reader o cualquier validador PDF '
        'compatible con firmas PKCS#7.',
        s['small'],
    ))

    story.append(Spacer(1, 0.8 * cm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=MID_GREY, spaceAfter=6))
    story.append(Paragraph(
        'Documento generado por CheckIt. Las evidencias contienen hash SHA-256 '
        'garantizando su integridad e inmutabilidad.',
        s['legal'],
    ))

    doc.build(story)
    with open(output_path, 'wb') as f:
        f.write(buffer.getvalue())
    return output_path
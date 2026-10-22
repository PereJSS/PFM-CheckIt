import io
import os
from urllib.parse import urlparse
from xml.sax.saxutils import escape

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


def _leer_foto(evidencia):
    """
    La foto en memoria, leída a través del storage (S3/R2 no tiene ruta local),
    o None si no está disponible.
    """
    if not evidencia.foto:
        return None
    try:
        with evidencia.foto.open('rb') as f:
            return io.BytesIO(f.read())
    except Exception:  # nosec B110 — el informe se genera igual, con un aviso en su lugar
        return None


def _host(url):
    return urlparse(url).hostname or url or '—'


def _horas_evidencia(ev):
    """Las tres horas de una evidencia y quién da cada una (#29): el sello prueba que la
    foto existía a esa hora; la de captura solo la declara el dispositivo."""
    def hora(fecha):
        return localtime(fecha).strftime('%d/%m/%Y %H:%M:%S %Z')

    partes = []
    if ev.capturada_en:
        partes.append(f'Capturada {hora(ev.capturada_en)} (según el dispositivo)')
    partes.append(f'Recibida {hora(ev.fecha_captura)} (servidor de CheckIt)')
    if ev.tsa_timestamp:
        partes.append(f'Sello TSA {hora(ev.tsa_timestamp)} (RFC 3161)')
    else:
        partes.append('Sello TSA pendiente')
    texto = ' · '.join(partes)
    avisos = ev.avisos_hora()
    if avisos:
        texto += f' · <b>Atención: hora del dispositivo {" y ".join(avisos)}</b>'
    return texto


def _seccion_manifiesto(inspeccion, s):
    """Raíz Merkle de la inspección y su sello de tiempo, o el motivo por el que falta."""
    flowables = [Paragraph('Manifiesto Merkle de la Inspección (RFC 6962)', s['h2'])]
    if not inspeccion.manifiesto_raiz:
        flowables.append(Paragraph(
            'Manifiesto pendiente: se genera y se sella al completar la inspección.', s['value'],
        ))
        return flowables

    num_hojas = len((inspeccion.manifiesto or {}).get('hojas', []))
    if inspeccion.manifiesto_tsa_token:
        ts = inspeccion.manifiesto_tsa_timestamp
        ts_str = ts.strftime('%d/%m/%Y %H:%M:%S UTC') if ts else '—'
        tipo = ('Sello de tiempo cualificado (eIDAS)' if inspeccion.manifiesto_tsa_cualificada
                else 'Sello de tiempo no cualificado')
        sello = f'{tipo} · {ts_str} · {_host(inspeccion.manifiesto_tsa_url)}'
        if not inspeccion.manifiesto_tsa_verificado:
            sello += ' · emisor no verificado contra un certificado de TSA de confianza'
    else:
        sello = 'Sello pendiente: la TSA no respondió; se reintenta automáticamente.'
    filas = [
        ['Evidencias incluidas', str(num_hojas)],
        ['Raíz SHA-256', Paragraph(inspeccion.manifiesto_raiz, s['small'])],
        ['Sello RFC 3161', Paragraph(sello, s['small'])],
    ]
    flowables.append(_kv_table(filas, col_widths=[4.5 * cm, PAGE_W - 2 * MARGIN - 4.5 * cm]))
    flowables.append(Spacer(1, 0.2 * cm))
    flowables.append(Paragraph(
        'La raíz resume los hashes de todas las evidencias en el orden en que se registraron: '
        'cambiar, añadir o quitar una foto cambia la raíz. Cada foto se puede comprobar por '
        'separado con su prueba de inclusión.',
        s['small'],
    ))
    return flowables


def generar_informe_base(inspeccion, output_path=None):
    """Genera el PDF base de la reclamación usando los campos reales del modelo."""
    if output_path is None:
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
    story: list = []

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
        story.append(Paragraph(
            'Solo el sello TSA prueba que cada foto existía a esa hora. La hora de captura la '
            'declara el dispositivo y nadie la certifica.', s['small'],
        ))
        for idx, ev in enumerate(evidencias, start=1):
            story.append(Paragraph(f'<b>Evidencia #{idx}</b>', s['value']))
            story.append(Paragraph(_horas_evidencia(ev), s['small']))
            # Texto del operario: escapado, Paragraph interpreta marcado
            story.append(Paragraph(escape(ev.descripcion or 'Sin descripción'), s['small']))

            imagen = _leer_foto(ev)
            if imagen is None:
                story.append(Paragraph('No se pudo cargar la foto en el informe (archivo no disponible).', s['small']))
            else:
                try:
                    story.append(Image(imagen, width=6 * cm, height=4.5 * cm, kind='proportional'))
                except Exception:
                    story.append(Paragraph('No se pudo cargar la foto en el informe (formato no compatible).', s['small']))

            story.append(Spacer(1, 0.4 * cm))

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

    # ── Manifiesto Merkle de la inspección ───────────────────────────────────
    story.extend(_seccion_manifiesto(inspeccion, s))

    # ── Firma Digital X.509 ──────────────────────────────────────────────────
    from . import pdf_signer, tsa

    story.append(Paragraph('Firma Digital X.509 (PAdES)', s['h2']))
    cert_path = os.getenv('PDF_SIGNING_PKCS12_PATH') or os.getenv('PDF_SIGNING_CERT_PATH', 'certs/signing.crt')
    if pdf_signer.sello_tiempo_activo():
        sello_firma = f"Solicitado a {_host(tsa.configuracion(tsa.PERFIL_MANIFIESTO)['url'])} (PAdES-B-T)"
    else:
        sello_firma = 'No (PAdES-B-B)'
    firma_rows = [
        ['Tipo de firma', 'PAdES / CMS (PDF con escritura incremental)'],
        ['Algoritmo', 'RSA + SHA-256'],
        ['Campo de firma', 'Firma_Pericial_CheckIt'],
        ['Certificado', os.path.basename(cert_path)],
        ['Sello de tiempo', sello_firma],
        ['Fecha de emisión', localtime(tz_now()).strftime('%d/%m/%Y %H:%M:%S')],
    ]
    story.append(_kv_table(firma_rows, col_widths=[4.5 * cm, PAGE_W - 2 * MARGIN - 4.5 * cm]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        'La firma digital X.509 integrada en este PDF garantiza su inmutabilidad legal desde el momento '
        'de la firma. Para verificarla, ábralo con Adobe Acrobat Reader o cualquier validador PDF '
        'compatible con firmas PAdES; el panel de firmas indica si lleva sello de tiempo (si la TSA '
        'no respondió al firmar, la firma se emite sin él).',
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
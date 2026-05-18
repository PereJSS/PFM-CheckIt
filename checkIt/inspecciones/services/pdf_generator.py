import os
from reportlab.pdfgen import canvas

def generar_informe_base(inspeccion):
    
   #Dibuja el PDF inicial de la reclamación con ReportLab.
    
    # Creamos una carpeta temporal en el backend para guardar los PDFs generados
    os.makedirs('tmp', exist_ok=True)
    output_path = f"tmp/base_reclamacion_{inspeccion.id}.pdf"

    # Dibujamos el contenido del PDF
    c = canvas.Canvas(output_path)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 800, "Ckeckii - Informe Pericial de Reclamación")
    
    c.setFont("Helvetica", 12)
    c.drawString(50, 760, f"ID de Inspección: {inspeccion.id}")
    c.drawString(50, 740, "Este documento contiene el hash criptográfico SHA-256")
    c.drawString(50, 720, "y está firmado digitalmente para evitar su manipulación.")
    
    c.save()
    return output_path
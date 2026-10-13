from celery import shared_task


@shared_task(bind=True, max_retries=3, default_retry_delay=60, time_limit=300, soft_time_limit=270)
def generar_pdf_async(self, inspeccion_id):
    from .models import Inspeccion
    from .services.pdf_generator import generar_informe_base
    from .services.pdf_signer import firmar_pdf_reclamacion

    inspeccion = Inspeccion.objects.get(pk=inspeccion_id)
    try:
        inspeccion.pdf_estado = Inspeccion.PdfEstado.GENERANDO
        inspeccion.save(update_fields=['pdf_estado'])

        input_pdf = generar_informe_base(inspeccion)
        ruta_final = f"tmp/Reclamacion_Fianza_{inspeccion.pk}_Firmada.pdf"
        firmar_pdf_reclamacion(input_pdf, ruta_final)

        inspeccion.pdf_estado = Inspeccion.PdfEstado.LISTO
        inspeccion.pdf_ruta = ruta_final
        inspeccion.save(update_fields=['pdf_estado', 'pdf_ruta'])
    except Exception as exc:
        inspeccion.pdf_estado = Inspeccion.PdfEstado.ERROR
        inspeccion.save(update_fields=['pdf_estado'])
        raise self.retry(exc=exc)

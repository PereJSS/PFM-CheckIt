"""Checklist de cada inspección (#19): copia fija de la plantilla y reglas de completado.

La copia se hace al crear la inspección (admin o webhook del PMS), dentro de la misma
transacción. El checklist queda fuera del manifiesto sellado: lo declara el operario.
"""
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q

from propiedades.models import PlantillaChecklist, normalizar_estructura

from ..models import ChecklistInspeccion, EstanciaInspeccion, Evidencia, PuntoInspeccion

SIN_MARCAR = 'sin_marcar'
FALTA_FOTO = 'falta_foto'


def plantilla_efectiva(propiedad):
    """La plantilla de la propiedad o, si no tiene, la por defecto de su cliente."""
    plantilla = propiedad.plantilla_checklist
    # Una plantilla de otro cliente no debería estar asignada; si lo está, no se usa
    if plantilla is not None and plantilla.owner_id == propiedad.owner_id:
        return plantilla
    if propiedad.owner_id is None:
        return None
    return PlantillaChecklist.objects.filter(owner_id=propiedad.owner_id, por_defecto=True).first()


def _checklist_de(inspeccion):
    return ChecklistInspeccion.objects.filter(inspeccion=inspeccion).first()


def instanciar_checklist(inspeccion, plantilla=None):
    """Copia la plantilla en la inspección. Idempotente; sin plantilla devuelve None.

    Hay que llamarla dentro de la transacción que crea la inspección."""
    existente = _checklist_de(inspeccion)
    if existente is not None:
        return existente
    plantilla = plantilla or plantilla_efectiva(inspeccion.propiedad)
    if plantilla is None:
        return None
    # Se vuelve a validar: una plantilla guardada sin full_clean (shell, datos viejos) no
    # debe meter en la inspección textos que la API rechazaría
    estructura = normalizar_estructura(plantilla.estructura)
    try:
        # Savepoint: si otra llamada (un webhook reintentado) la creó a la vez, el
        # OneToOne lo detecta y no se rompe la transacción de quien llama
        with transaction.atomic():
            checklist = ChecklistInspeccion.objects.create(
                inspeccion=inspeccion,
                plantilla=plantilla,
                plantilla_nombre=plantilla.nombre,
                plantilla_version=plantilla.version,
            )
    except IntegrityError:
        existente = _checklist_de(inspeccion)
        if existente is None:
            raise  # no era el OneToOne: que se vea el error real
        return existente
    estancias = EstanciaInspeccion.objects.bulk_create([
        EstanciaInspeccion(checklist=checklist, orden=i, nombre=e['nombre'])
        for i, e in enumerate(estructura['estancias'])
    ])
    PuntoInspeccion.objects.bulk_create([
        PuntoInspeccion(
            estancia=estancia, inspeccion=inspeccion, orden=j,
            texto=p['texto'], requiere_foto=p['requiere_foto'],
        )
        for estancia, e in zip(estancias, estructura['estancias'])
        for j, p in enumerate(e['puntos'])
    ])
    return checklist


def _exige_foto(punto):
    R = PuntoInspeccion.Resultado
    return punto.resultado == R.DANO or (punto.requiere_foto and punto.resultado == R.OK)


def faltas_para_completar(inspeccion_id):
    """Lo que impide completar: puntos sin marcar y daños (o puntos con foto obligatoria) sin foto.

    «No aplica» nunca exige foto. Sin checklist, lista vacía."""
    puntos = (
        PuntoInspeccion.objects.filter(inspeccion_id=inspeccion_id)
        .select_related('estancia')
        .annotate(n_fotos=Count('evidencias'))
        .order_by('estancia__orden', 'orden')
    )
    faltas = []
    for punto in puntos:
        if punto.resultado is None:
            motivo = SIN_MARCAR
        elif punto.n_fotos == 0 and _exige_foto(punto):
            motivo = FALTA_FOTO
        else:
            continue
        faltas.append({
            'punto': punto.pk, 'estancia': punto.estancia.nombre, 'texto': punto.texto, 'motivo': motivo,
        })
    return faltas


def resumen(inspeccion_id):
    """Recuento por resultado, o None si la inspección no tiene checklist."""
    if not ChecklistInspeccion.objects.filter(inspeccion_id=inspeccion_id).exists():
        return None
    R = PuntoInspeccion.Resultado
    return PuntoInspeccion.objects.filter(inspeccion_id=inspeccion_id).aggregate(
        puntos=Count('id'),
        ok=Count('id', filter=Q(resultado=R.OK)),
        danos=Count('id', filter=Q(resultado=R.DANO)),
        no_aplica=Count('id', filter=Q(resultado=R.NO_APLICA)),
        sin_marcar=Count('id', filter=Q(resultado__isnull=True)),
    )


def checklist_canonico(inspeccion):
    """El checklist como JSON determinista (orden fijo), para el expediente, el webhook y
    un futuro manifiesto v2. None si no hay checklist."""
    checklist = (
        ChecklistInspeccion.objects.filter(inspeccion=inspeccion)
        .prefetch_related(Prefetch(
            'estancias__puntos__evidencias', queryset=Evidencia.objects.order_by('pk').only('pk', 'punto_id'),
        ))
        .first()
    )
    if checklist is None:
        return None
    return {
        'formato': checklist.formato,
        'plantilla': checklist.plantilla_nombre,
        'plantilla_version': checklist.plantilla_version,
        'estancias': [
            {
                'nombre': estancia.nombre,
                'puntos': [
                    {
                        'id': punto.pk,
                        'texto': punto.texto,
                        'requiere_foto': punto.requiere_foto,
                        'resultado': punto.resultado,
                        'nota': punto.nota,
                        'marcado_en': punto.marcado_en.isoformat() if punto.marcado_en else None,
                        'evidencias': [ev.pk for ev in punto.evidencias.all()],
                    }
                    for punto in estancia.puntos.all()
                ],
            }
            for estancia in checklist.estancias.all()
        ],
    }

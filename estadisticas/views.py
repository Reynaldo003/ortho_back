# estadisticas/views.py
from datetime import date, datetime, timedelta

from django.db.models import Count, Sum, F, Case, When, DecimalField, Value
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth, TruncYear
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from core.models import Paciente, Cita, Pago


def _parse_date(s, default=None):
    if not s:
        return default
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return default


def _get_trunc(group):
    # group: day|week|month|year
    if group == "day":
        return TruncDay
    if group == "week":
        return TruncWeek
    if group == "year":
        return TruncYear
    return TruncMonth  # default


def _iso(d):
    # date/datetime -> str
    if d is None:
        return None
    try:
        return d.date().isoformat()
    except Exception:
        return d.isoformat()


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def estadisticas(request):
    today = date.today()

    # Defaults: mes actual
    default_from = today.replace(day=1)
    default_to = today

    from_q = _parse_date(request.query_params.get("from"), default_from)
    to_q = _parse_date(request.query_params.get("to"), default_to)

    # normaliza orden
    if from_q > to_q:
        from_q, to_q = to_q, from_q

    group = (request.query_params.get("group") or "month").strip().lower()
    if group not in ("day", "week", "month", "year"):
        group = "month"

    profesional_id = request.query_params.get("profesional")
    try:
        profesional_id = int(profesional_id) if profesional_id else None
    except Exception:
        profesional_id = None

    trunc = _get_trunc(group)

    # Rango inclusivo (para DateField es OK usar __range)
    citas_base = Cita.objects.filter(fecha__range=(from_q, to_q))
    #pagos_base = Pago.objects.filter(fecha_pago__range=(from_q, to_q))
    pagos_base = Pago.objects.filter(cita__fecha__range=(from_q, to_q))
    pacientes_base = Paciente.objects.all()

    # filtro por profesional (afecta citas y pagos vía cita)
    if profesional_id:
        citas_base = citas_base.filter(profesional_id=profesional_id)
        pagos_base = pagos_base.filter(cita__profesional_id=profesional_id)

    # =========================
    # 1) Asistencias (citas completadas) por periodo
    # =========================
    attendance_series = (
        citas_base.filter(estado="completado")
        .annotate(period=trunc("fecha"))
        .values("period")
        .annotate(total=Count("id"))
        .order_by("period")
    )

    # =========================
    # 2) Estado de cita
    # =========================
    status_breakdown = (
        citas_base.values("estado")
        .annotate(count=Count("id"))
        .order_by("estado")
    )

    # =========================
    # 3) Ventas realizadas (pagos) por periodo (cuenta de pagos y total cobrado)
    # =========================
    sales_series = (
        pagos_base.annotate(period=trunc("cita__fecha"))
        .values("period")
        .annotate(total_pagos=Count("id"), total_cobrado=Sum("anticipo"))
        .order_by("period")
    )
    # =========================
    # 4) Métodos de pago (por pagos)
    # =========================
    payments_by_method = (
        pagos_base.values("metodo_pago")
        .annotate(
            total=Sum("anticipo"),
            count=Count("id"),
        )
        .order_by("-total")
    )

    # =========================
    # 5) Ingresos por servicio (cobrado) usando pagos + relación con servicio
    # =========================
    revenue_by_service = (
        pagos_base.values("cita__servicio__nombre")
        .annotate(total=Sum("anticipo"), count=Count("id"))
        .order_by("-total")
    )

    # =========================
    # 6) Ingresos totales mensuales (cobrado)
    #     (si group != month, igual devolvemos mensual para ese gráfico)
    # =========================
    monthly_income = (
        pagos_base.annotate(period=TruncMonth("cita__fecha"))
        .values("period")
        .annotate(total=Sum("anticipo"))
        .order_by("period")
    )

    # =========================
    # 7) Pacientes: alta vs en tratamiento (totales) + altas por periodo
    # =========================
    patient_status_totals = (
        pacientes_base.values("estado_tratamiento")
        .annotate(count=Count("id"))
        .order_by("estado_tratamiento")
    )

    # altas dentro del rango (para gráfica)
    patients_alta_series = (
        pacientes_base.filter(estado_tratamiento="alta", fecha_alta__range=(from_q, to_q))
        .annotate(period=trunc("fecha_alta"))
        .values("period")
        .annotate(total=Count("id"))
        .order_by("period")
    )

    # =========================
    # 8) KPIs rápidos
    # =========================
    total_cobrado = pagos_base.aggregate(t=Sum("anticipo")).get("t") or 0
    total_pagos = pagos_base.count()

    total_asistencias = citas_base.filter(estado="completado").count()
    total_citas = citas_base.count()

    # pacientes nuevos en rango (por registro)
    pacientes_nuevos = (
        pacientes_base.filter(registro__range=(from_q, to_q)).count()
    )

    return Response(
        {
            "range": {"from": from_q.isoformat(), "to": to_q.isoformat()},
            "group": group,
            "profesional": profesional_id,

            # series
            "attendance_series": list(attendance_series),
            "sales_series": list(sales_series),
            "monthly_income": list(monthly_income),

            # breakdowns
            "status_breakdown": list(status_breakdown),
            "payments_by_method": list(payments_by_method),
            "revenue_by_service": list(revenue_by_service),

            # pacientes
            "patient_status_totals": list(patient_status_totals),
            "patients_alta_series": list(patients_alta_series),

            # kpis
            "kpis": {
                "total_cobrado": float(total_cobrado),
                "total_pagos": total_pagos,
                "total_asistencias": total_asistencias,
                "total_citas": total_citas,
                "pacientes_nuevos": pacientes_nuevos,
            },
        }
    )

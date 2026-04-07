from datetime import datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from io import BytesIO
import os
import re
import smtplib
import textwrap
import unicodedata

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.db import models
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from rest_framework import permissions, viewsets, status, mixins
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    Paciente,
    Comentario,
    Cita,
    Servicio,
    Clinica,
    Pago,
    StaffProfile,
    BloqueoHorario,
    Insumo,
    MovimientoInsumo,
    ExpedienteClinico,
    SesionClinica,
    NotaClinica,
    RecetaMedica,
    EvidenciaClinica,
)
from .permissions import IsAdminUserStrict
from .serializers import (
    PacienteSerializer,
    ComentarioSerializer,
    ComentarioPublicSerializer,
    CitaSerializer,
    ServicioSerializer,
    UserSerializer,
    CitaCreateSerializer,
    PagoSerializer,
    StaffUserSerializer,
    BloqueoHorarioSerializer,
    InsumoSerializer,
    MovimientoInsumoSerializer,
    ExpedienteClinicoSerializer,
    SesionClinicaSerializer,
    NotaClinicaSerializer,
    RecetaMedicaSerializer,
    EvidenciaClinicaSerializer,
)

PUBLIC_DEFAULT_PRO_NAME = "l.f.t edgar mauricio medina cruz"
PUBLIC_FERNANDO_NAMES = (
    "jose fernando porras pulido",
    "fernando porras pulido",
    "jose fernando porras",
    "fernando porras",
)

ROLE_ALIASES = {
    "doctor": "doctor",
    "medico": "doctor",
    "fisioterapeuta": "fisioterapeuta",
    "aux_fisioterapia": "aux_fisioterapia",
    "auxiliar_fisioterapia": "aux_fisioterapia",
    "subfisioterapeuta": "aux_fisioterapia",
    "sub_fisioterapeuta": "aux_fisioterapia",
    "recepcionista": "recepcionista",
    "recepcion": "recepcionista",
    "admin": "doctor",
    "dentista": "doctor",
    "nutriologo": "doctor",
    "colaborador": "recepcionista",
}
PASSWORD_RESET_ALERT_EMAIL = "OCC.administracion@gmail.com"
INSUMOS_ALERT_EMAIL = "OCC.insumos@gmail.com"


def _send_email_smtp(*, asunto: str, mensaje: str, destinatario: str) -> bool:
    remitente = getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "EMAIL_HOST_USER", "")
    smtp_host = getattr(settings, "EMAIL_HOST", "")
    smtp_port = getattr(settings, "EMAIL_PORT", 465)
    smtp_user = getattr(settings, "EMAIL_HOST_USER", "")
    smtp_password = getattr(settings, "EMAIL_HOST_PASSWORD", "")

    if not (remitente and destinatario and smtp_host and smtp_user and smtp_password):
        print("[EMAIL] Faltan datos SMTP para enviar correo.")
        return False

    try:
        email = EmailMessage()
        email["From"] = remitente
        email["To"] = destinatario
        email["Subject"] = asunto
        email.set_content(mensaje)

        smtp = smtplib.SMTP_SSL(smtp_host, smtp_port)
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(email)
        smtp.quit()
        return True
    except Exception as exc:
        print("[EMAIL] Error enviando correo SMTP:", repr(exc))
        return False


def _notificar_stock_bajo_si_aplica(insumo):
    cantidad = int(insumo.cantidad or 0)
    minimo = int(insumo.minimo or 0)
    bajo_stock = cantidad <= minimo

    # ✅ si ya salió de bajo stock, reiniciamos la bandera
    if not bajo_stock:
        cambios = []

        if insumo.alerta_stock_bajo_enviada:
            insumo.alerta_stock_bajo_enviada = False
            cambios.append("alerta_stock_bajo_enviada")

        if insumo.alerta_stock_bajo_fecha is not None:
            insumo.alerta_stock_bajo_fecha = None
            cambios.append("alerta_stock_bajo_fecha")

        if cambios:
            cambios.append("actualizado")
            insumo.save(update_fields=cambios)

        return False

    # ✅ si ya se avisó y sigue bajo, no vuelvas a mandar el correo
    if insumo.alerta_stock_bajo_enviada:
        return False

    clinica_nombre = getattr(getattr(insumo, "clinica", None), "nombre", "") or "Sin clínica"
    asunto = f"Alerta de stock mínimo - {insumo.nombre}"
    mensaje = (
        "Se detectó un insumo en stock mínimo o por debajo del mínimo.\n\n"
        f"Clínica: {clinica_nombre}\n"
        f"Insumo: {insumo.nombre}\n"
        f"Categoría: {insumo.categoria}\n"
        f"Cantidad actual: {cantidad}\n"
        f"Stock mínimo: {minimo}\n"
        f"Notas: {insumo.notas or 'Sin notas'}\n\n"
        "Favor de revisar y reabastecer el producto."
    )

    enviado = _send_email_smtp(
        asunto=asunto,
        mensaje=mensaje,
        destinatario=INSUMOS_ALERT_EMAIL,
    )

    if enviado:
        insumo.alerta_stock_bajo_enviada = True
        insumo.alerta_stock_bajo_fecha = timezone.now()
        insumo.save(
            update_fields=[
                "alerta_stock_bajo_enviada",
                "alerta_stock_bajo_fecha",
                "actualizado",
            ]
        )

    return enviado

def _tipo_archivo_desde_nombre(nombre):
    nombre = (nombre or "").lower()
    if nombre.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "imagen"
    if nombre.endswith(".pdf"):
        return "pdf"
    return "otro"


def _normalize_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = " ".join(s.split())
    return s


def _normalize_role_value(role, fallback="recepcionista"):
    key = str(role or "").strip().lower()
    if not key:
        return fallback
    return ROLE_ALIASES.get(key, fallback)


def _user_role(user):
    if not user or not user.is_authenticated:
        return None

    sp = getattr(user, "staff_profile", None)
    if sp and sp.rol:
        return _normalize_role_value(sp.rol)

    if user.is_superuser or user.is_staff:
        return "doctor"
    return "recepcionista"


def _normalize_person_lookup(value):
    value = _normalize_name(value or "")
    value = re.sub(r"[^a-z0-9\s]", " ", value)

    stopwords = {
        "dr",
        "dra",
        "doctor",
        "doctora",
        "lic",
        "licenciado",
        "licenciada",
        "lft",
        "ltf",
        "ft",
        "fisio",
        "med",
        "medico",
        "medica",
        "mtro",
        "mtra",
    }

    tokens = [token for token in value.split() if token not in stopwords]
    return " ".join(tokens)


def _safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _parse_time_text(value):
    if value is None or value == "":
        return None

    if hasattr(value, "hour") and hasattr(value, "minute"):
        return value

    text = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass

    return None


def _find_staff_user_by_lookup(text, role=None):
    target = _normalize_person_lookup(text)
    if not target:
        return None

    qs = User.objects.filter(is_active=True, staff_profile__isnull=False).order_by("id")
    if role:
        qs = qs.filter(staff_profile__rol=role)

    best_user = None
    best_score = 0
    target_tokens = set(target.split())

    for user in qs:
        candidates = [
            user.get_full_name(),
            f"{user.first_name or ''} {user.last_name or ''}".strip(),
            user.username,
        ]

        for candidate in candidates:
            current = _normalize_person_lookup(candidate)
            if not current:
                continue

            if current == target:
                return user

            current_tokens = set(current.split())
            overlap = len(current_tokens & target_tokens)
            if not overlap:
                continue

            score = overlap
            if current in target or target in current:
                score += 5

            if score > best_score:
                best_score = score
                best_user = user

    return best_user


def _find_public_fernando():
    for possible_name in PUBLIC_FERNANDO_NAMES:
        found = _find_staff_user_by_lookup(possible_name, role="fisioterapeuta")
        if found:
            return found
    return None


def _resolve_public_professional(
    clinica,
    *,
    agenda_tipo="general",
    profesional_id=None,
    profesional_nombre="",
    profesional_slug="",
):
    explicit_id = _safe_int(profesional_id)
    if explicit_id:
        return User.objects.filter(
            id=explicit_id,
            is_active=True,
            staff_profile__isnull=False,
        ).first()

    explicit_text = (profesional_nombre or profesional_slug or "").strip()
    if explicit_text:
        return _find_staff_user_by_lookup(explicit_text)

    if agenda_tipo in ("acondicionamiento", "terapia"):
        return _find_public_fernando()

    return _default_public_professional(clinica)


def _exists_block_conflict(*, profesional_id, fecha, hora_inicio, hora_termina, exclude_id=None):
    qs = BloqueoHorario.objects.filter(profesional_id=profesional_id, fecha=fecha)

    if exclude_id:
        qs = qs.exclude(id=exclude_id)

    for block in qs.only("hora_inicio", "hora_termina"):
        if _overlaps(hora_inicio, hora_termina, block.hora_inicio, block.hora_termina):
            return True

    return False


def _validate_professional_schedule(
    *,
    profesional_id,
    fecha,
    hora_inicio,
    hora_termina,
    cita_exclude_id=None,
    bloqueo_exclude_id=None,
):
    if not fecha:
        raise ValidationError({"fecha": "La fecha es requerida."})

    if not hora_inicio or not hora_termina:
        raise ValidationError({"detail": "La hora de inicio y la hora de término son requeridas."})

    if hora_termina <= hora_inicio:
        raise ValidationError({"detail": "La hora de término debe ser mayor que la hora de inicio."})

    if _validar_conflicto_cita(
        profesional_id=profesional_id,
        fecha=fecha,
        hora_inicio=hora_inicio,
        hora_termina=hora_termina,
        exclude_id=cita_exclude_id,
    ):
        raise ValidationError({"detail": "Ese horario ya está ocupado para ese profesional."})

    if _exists_block_conflict(
        profesional_id=profesional_id,
        fecha=fecha,
        hora_inicio=hora_inicio,
        hora_termina=hora_termina,
        exclude_id=bloqueo_exclude_id,
    ):
        raise ValidationError({"detail": "Ese horario está bloqueado para ese profesional."})


def _is_admin_like(role: str) -> bool:
    return _normalize_role_value(role) in ("doctor", "fisioterapeuta")


def _can_see_all_agendas(role: str) -> bool:
    return _normalize_role_value(role) in ("doctor", "fisioterapeuta", "recepcionista")


def _is_professional_role(role: str) -> bool:
    return _normalize_role_value(role) in ("doctor", "fisioterapeuta", "aux_fisioterapia")


def _must_limit_to_own_records(role: str) -> bool:
    return _normalize_role_value(role) == "aux_fisioterapia"


def _first_clinica():
    return Clinica.objects.first()


def _calc_hora_termina(fecha_str, hora_inicio_str, duracion_td):
    dt = datetime.fromisoformat(f"{fecha_str}T{hora_inicio_str}")
    dt_end = dt + (duracion_td or timedelta(minutes=60))
    return dt_end.time()


def _overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def _validar_conflicto_cita(*, profesional_id, fecha, hora_inicio, hora_termina, exclude_id=None):
    qs = Cita.objects.filter(
        profesional_id=profesional_id,
        fecha=fecha,
    ).exclude(estado="cancelado")

    if exclude_id:
        qs = qs.exclude(id=exclude_id)

    for cita in qs.only("hora_inicio", "hora_termina"):
        if _overlaps(hora_inicio, hora_termina, cita.hora_inicio, cita.hora_termina):
            return True

    return False


def _default_public_professional(clinica: Clinica):
    target = _normalize_name(PUBLIC_DEFAULT_PRO_NAME)
    qs = User.objects.filter(is_active=True, staff_profile__isnull=False).order_by("id")

    for user in qs:
        full = _normalize_name(f"{user.first_name} {user.last_name}")
        if full == target:
            return user

        if _normalize_name(user.username) == target:
            return user

    return getattr(clinica, "propietario", None)


def _normalizar_color_hex(valor, fallback="#06b6d4"):
    v = str(valor or "").strip()

    if re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        return v.lower()

    if re.fullmatch(r"#[0-9a-fA-F]{3}", v):
        return f"#{v[1]*2}{v[2]*2}{v[3]*2}".lower()

    return fallback


def _password_fuerte(pw: str) -> bool:
    if not pw or len(pw) < 8:
        return False
    if not re.search(r"[A-Z]", pw):
        return False
    if not re.search(r"[a-z]", pw):
        return False
    if not re.search(r"[0-9]", pw):
        return False
    if not re.search(r"[^A-Za-z0-9]", pw):
        return False
    return True


def _clinica_for_user(user):
    perfil = getattr(user, "perfil", None)
    if perfil and getattr(perfil, "clinica_id", None):
        return perfil.clinica
    return _first_clinica()


def _media_file_path(filename: str):
    media_root = getattr(settings, "MEDIA_ROOT", "")
    if not media_root:
        return None

    path = os.path.join(str(media_root), filename)
    return path if os.path.exists(path) else None


def _build_ticket_response(pago):
    cita = pago.cita
    clinica = _first_clinica()
    paciente = cita.paciente
    profesional = cita.profesional

    rfc_emisor = "MECE000513F74"
    tasa_iva = Decimal("0.16")
    total = Decimal(cita.monto_final or pago.monto_facturado or cita.precio or 0)
    total = max(total, Decimal("0"))
    subtotal = (total / (Decimal("1.00") + tasa_iva)) if total > 0 else Decimal("0")
    iva = total - subtotal

    descuento_pct = Decimal(cita.descuento_porcentaje or pago.descuento_porcentaje or 0)
    descuento_base = Decimal(pago.monto_facturado or cita.precio or 0)
    descuento_monto = (descuento_base * descuento_pct) / Decimal("100")

    pagos_qs = cita.pagos.all()
    total_pagado = pagos_qs.aggregate(total=models.Sum("anticipo")).get("total") or Decimal("0")
    restante = max(total - Decimal(total_pagado), Decimal("0"))

    by_method = (
        pagos_qs.values("metodo_pago")
        .annotate(total=models.Sum("anticipo"))
        .order_by("metodo_pago")
    )

    logo_path = _media_file_path("logo.png")
    qr_path = _media_file_path("qr.png")

    def up(value):
        return (value or "").strip().upper()

    def safe(value):
        return (value or "").strip()

    def money(value):
        amount = Decimal(value or 0)
        return f"$ {amount:,.2f}"

    def wrap(text, width=32):
        text = up(text)
        if not text:
            return []
        return textwrap.wrap(text, width=width, break_long_words=True, break_on_hyphens=False)

    paciente_nombre = f"{paciente.nombres} {paciente.apellido_pat} {paciente.apellido_mat or ''}".strip()
    profesional_nombre = (
        f"{profesional.first_name or ''} {profesional.last_name or ''}".strip()
        or profesional.username
    )
    servicio_nombre = safe(getattr(cita.servicio, "nombre", "")) or "SERVICIO"
    clinica_nombre = safe(getattr(clinica, "nombre", "")) or "ORTHO CLINIC"
    direccion = safe(getattr(clinica, "direccion", "")) or "DIRECCIÓN NO CONFIGURADA"

    now = timezone.now()
    fecha_emision = now.strftime("%d/%m/%Y")
    hora_emision = now.strftime("%H:%M:%S")

    factura_lines = []
    if getattr(paciente, "requiere_factura", False):
        factura_lines.append("DATOS DE FACTURACIÓN")
        factura_pairs = [
            ("RAZÓN SOCIAL", paciente.facturacion_razon_social),
            ("RFC", paciente.facturacion_rfc),
            ("RÉGIMEN", paciente.facturacion_regimen_fiscal),
            ("C.P.", paciente.facturacion_codigo_postal),
            ("USO CFDI", paciente.facturacion_uso_cfdi),
            ("CORREO", paciente.facturacion_correo),
        ]
        for label, value in factura_pairs:
            text = safe(value)
            if text:
                factura_lines.extend(wrap(f"{label}: {text}", 32))

    pagos_lines = []
    labels = {
        "efectivo": "EFECTIVO",
        "tarjeta": "TARJETA",
        "transferencia": "TRANSFERENCIA",
        "otro": "OTRO",
    }
    for row in by_method:
        pagos_lines.append((labels.get((row.get("metodo_pago") or "").strip(), up(row.get("metodo_pago") or "OTRO")), money(row.get("total") or 0)))

    if not pagos_lines:
        pagos_lines.append(("SIN PAGOS", money(0)))

    ticket_width = 80 * mm
    line_h = 4.1 * mm
    top_pad = 8 * mm
    bottom_pad = 8 * mm
    sep = "-" * 32

    logo_h = 0
    if logo_path:
        logo_h = 18 * mm

    qr_h = 18 * mm if qr_path else 0

    text_lines = (
        2
        + len(wrap(direccion, 32))
        + 3
        + 2
        + len(wrap(paciente_nombre, 32))
        + 2
        + len(wrap(profesional_nombre, 32))
        + 2
        + len(wrap(servicio_nombre, 32))
        + 6
        + 1
        + len(pagos_lines)
        + len(factura_lines)
        + 3
    )

    height = top_pad + bottom_pad + (text_lines * line_h) + logo_h + qr_h + (10 * mm)
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(ticket_width, height))
    y = height - top_pad

    def draw_center(text, font="Helvetica", size=8.5):
        nonlocal y
        pdf.setFont(font, size)
        text = up(text)
        text_width = pdf.stringWidth(text, font, size)
        x = max(2 * mm, (ticket_width - text_width) / 2)
        pdf.drawString(x, y, text)
        y -= line_h

    def draw_lr(left, right="", font="Helvetica", size=8.5):
        nonlocal y
        pdf.setFont(font, size)
        left = up(left)
        right = up(right)
        left_x = 4 * mm
        right_x = ticket_width - 4 * mm
        pdf.drawString(left_x, y, left)
        if right:
            right_w = pdf.stringWidth(right, font, size)
            pdf.drawString(right_x - right_w, y, right)
        y -= line_h

    if logo_path:
        try:
            img = ImageReader(logo_path)
            draw_w = 48 * mm
            draw_h = 18 * mm
            x = (ticket_width - draw_w) / 2
            pdf.drawImage(
                img,
                x,
                y - draw_h,
                width=draw_w,
                height=draw_h,
                preserveAspectRatio=True,
                mask="auto",
            )
            y -= draw_h + (2 * mm)
        except Exception:
            pass

    draw_center(clinica_nombre, "Helvetica-Bold", 9)
    draw_center(f"RFC: {rfc_emisor}", "Helvetica", 8.3)

    for line in wrap(direccion, 32):
        draw_center(line, "Helvetica", 8.0)

    draw_center(sep)
    draw_center(f"VENTA: {cita.id}  TICKET: {pago.id}")
    draw_center(f"EMISIÓN: {fecha_emision} {hora_emision}")
    draw_center(sep)

    draw_center("CLIENTE", "Helvetica-Bold")
    for line in wrap(paciente_nombre, 32):
        draw_center(line)

    draw_center("PROFESIONAL", "Helvetica-Bold")
    for line in wrap(profesional_nombre, 32):
        draw_center(line)

    draw_center(sep)
    draw_center("DETALLE", "Helvetica-Bold")
    for line in wrap(servicio_nombre, 32):
        draw_center(line)

    draw_center(sep)
    draw_lr("PRECIO LISTA", money(cita.precio or 0))
    draw_lr(f"DESC. ({descuento_pct}%)", f"-{money(descuento_monto)}")
    draw_lr("SUBTOTAL", money(subtotal))
    draw_lr("IVA 16%", money(iva))
    draw_lr("TOTAL", money(total), font="Helvetica-Bold")
    draw_lr("COBRADO", money(total_pagado))
    draw_lr("RESTANTE", money(restante), font="Helvetica-Bold")

    draw_center(sep)
    draw_center("PAGOS POR MÉTODO", "Helvetica-Bold")
    for label, amount in pagos_lines:
        draw_lr(label, amount)

    if factura_lines:
        draw_center(sep)
        for line in factura_lines:
            draw_center(line, "Helvetica", 8.0)

    draw_center(sep)
    draw_center("GRACIAS POR SU PREFERENCIA", "Helvetica-Bold", 8.5)
    draw_center("DOCUMENTO GENERADO POR EL SISTEMA", "Helvetica", 8.0)

    if qr_path:
        try:
            img_qr = ImageReader(qr_path)
            qr_w = 18 * mm
            x_qr = ticket_width - qr_w - (4 * mm)
            pdf.drawImage(
                img_qr,
                x_qr,
                4 * mm,
                width=qr_w,
                height=18 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            pass

    pdf.showPage()
    pdf.save()

    pdf_bytes = buffer.getvalue()
    buffer.close()

    filename = f"ticket_venta_{cita.id}_pago_{pago.id}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def public_team(request):
    qs = User.objects.filter(is_active=True, staff_profile__isnull=False).order_by("-id")
    serializer = StaffUserSerializer(qs, many=True, context={"request": request})
    return Response(serializer.data)


class StaffUserViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = StaffUserSerializer
    permission_classes = [IsAdminUserStrict]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        return User.objects.filter(is_active=True).order_by("-id")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx


class ProfesionalViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = (
            User.objects.filter(is_active=True, staff_profile__isnull=False)
            .select_related("staff_profile")
            .order_by("first_name", "last_name", "username")
        )

        agenda_scope = (self.request.query_params.get("agenda_scope") or "").strip().lower()
        rol = (self.request.query_params.get("rol") or "").strip().lower()
        roles = [
            _normalize_role_value(value)
            for value in (self.request.query_params.get("roles") or "").split(",")
            if str(value).strip()
        ]

        if agenda_scope == "principal":
            qs = qs.filter(staff_profile__rol="doctor")
        elif agenda_scope in ("terapia", "acondicionamiento"):
            qs = qs.filter(staff_profile__rol__in=["fisioterapeuta", "aux_fisioterapia"])

        if rol:
            qs = qs.filter(staff_profile__rol=_normalize_role_value(rol))

        if roles:
            qs = qs.filter(staff_profile__rol__in=roles)

        return qs


class PacienteViewSet(viewsets.ModelViewSet):
    serializer_class = PacienteSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        role = _user_role(self.request.user)
        qs = (
            Paciente.objects.select_related("clinica", "expediente")
            .prefetch_related("sesiones_clinicas", "citas")
            .all()
        )

        clinica = _clinica_for_user(self.request.user)
        if clinica:
            qs = qs.filter(clinica=clinica)

        if _must_limit_to_own_records(role):
            qs = qs.filter(citas__profesional=self.request.user).distinct()

        return qs.order_by("nombres", "apellido_pat", "apellido_mat")

    def perform_create(self, serializer):
        clinica = _clinica_for_user(self.request.user)
        if not clinica:
            raise ValidationError({"detail": "No existe clínica configurada para este usuario."})
        serializer.save(clinica=clinica)

    @action(detail=True, methods=["get"], url_path="historial-clinico")
    def historial_clinico(self, request, pk=None):
        paciente = self.get_object()
        sesiones = paciente.sesiones_clinicas.select_related("profesional", "cita").all()
        serializer = SesionClinicaSerializer(sesiones, many=True)
        return Response(serializer.data)


class SesionClinicaViewSet(viewsets.ModelViewSet):
    serializer_class = SesionClinicaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        role = _user_role(self.request.user)
        qs = SesionClinica.objects.select_related("paciente", "cita", "profesional")

        clinica = _clinica_for_user(self.request.user)
        if clinica:
            qs = qs.filter(paciente__clinica=clinica)

        paciente_id = self.request.query_params.get("paciente")
        if paciente_id:
            qs = qs.filter(paciente_id=paciente_id)

        cita_id = self.request.query_params.get("cita")
        if cita_id:
            qs = qs.filter(cita_id=cita_id)

        if _must_limit_to_own_records(role):
            qs = qs.filter(profesional=self.request.user)

        return qs.order_by("-fecha", "-id")

    def perform_create(self, serializer):
        role = _user_role(self.request.user)
        profesional = self.request.user if _is_professional_role(role) else None
        profesional_id = self.request.data.get("profesional")

        if profesional_id and _can_see_all_agendas(role):
            profesional = User.objects.filter(id=profesional_id).first()

        serializer.save(profesional=profesional)


class ComentarioViewSet(viewsets.ModelViewSet):
    queryset = (
        Comentario.objects.select_related("clinica", "profesional", "profesional__staff_profile", "servicio")
        .all()
        .order_by("-creado", "-id")
    )

    def get_permissions(self):
        if self.action in ["create", "public_list"]:
            return [permissions.AllowAny()]
        return [IsAdminUserStrict()]

    def get_serializer_class(self):
        if self.action == "public_list":
            return ComentarioPublicSerializer
        return ComentarioSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def perform_create(self, serializer):
        serializer.save(aprobado=False, clinica=_first_clinica())

    @action(detail=False, methods=["get"], permission_classes=[permissions.AllowAny])
    def public_list(self, request):
        queryset = self.get_queryset().filter(aprobado=True)

        tipo_objetivo = (request.query_params.get("tipo_objetivo") or "").strip()
        profesional_id = (request.query_params.get("profesional") or "").strip()
        servicio_id = (request.query_params.get("servicio") or "").strip()

        if tipo_objetivo in ["profesional", "servicio"]:
            queryset = queryset.filter(tipo_objetivo=tipo_objetivo)
        if profesional_id:
            queryset = queryset.filter(profesional_id=profesional_id)
        if servicio_id:
            queryset = queryset.filter(servicio_id=servicio_id)

        serializer = ComentarioPublicSerializer(queryset[:50], many=True, context={"request": request})
        return Response(serializer.data)

    @action(detail=False, methods=["get"], permission_classes=[IsAdminUserStrict])
    def pending(self, request):
        qs = self.get_queryset().filter(aprobado=False)
        serializer = ComentarioSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["patch"], permission_classes=[IsAdminUserStrict])
    def moderate(self, request, pk=None):
        obj = self.get_object()
        estado = (request.data.get("estado") or "").lower().strip()

        if estado == "aprobado":
            obj.aprobado = True
            obj.save(update_fields=["aprobado"])
            return Response(ComentarioSerializer(obj, context={"request": request}).data, status=status.HTTP_200_OK)

        if estado == "rechazado":
            obj.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(
            {"detail": "estado inválido. Usa 'aprobado' o 'rechazado'."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class CitaViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        role = _user_role(self.request.user)
        agenda_tipo = (self.request.query_params.get("agenda_tipo") or "").strip()

        qs = Cita.objects.select_related("paciente", "servicio", "profesional").order_by("fecha", "hora_inicio")

        if agenda_tipo:
            qs = qs.filter(agenda_tipo=agenda_tipo)

            if agenda_tipo == "acondicionamiento":
                if _must_limit_to_own_records(role):
                    qs = qs.filter(profesional=self.request.user)
                return qs

            if agenda_tipo == "terapia":
                return qs

            if _must_limit_to_own_records(role):
                qs = qs.filter(profesional=self.request.user)
            return qs

        if _must_limit_to_own_records(role):
            qs = qs.filter(profesional=self.request.user)

        return qs

    def get_serializer_class(self):
        if self.action == "create":
            data = self.request.data
            if isinstance(data.get("paciente"), dict):
                return CitaCreateSerializer
        return CitaSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["clinica"] = _first_clinica()
        ctx["request"] = self.request
        return ctx

    def _resolve_service_obj(self, data, serializer):
        servicio_id = data.get("servicio") or data.get("servicio_id")

        if servicio_id not in (None, "", "null"):
            return Servicio.objects.filter(id=servicio_id).first()

        instance = getattr(serializer, "instance", None)
        if instance and instance.servicio_id:
            return instance.servicio

        return None

    def _resolve_profesional_obj(self, data, serializer):
        profesional_id_payload = data.get("profesional")

        if profesional_id_payload not in (None, "", "null"):
            profesional_id = _safe_int(profesional_id_payload)
            if not profesional_id:
                raise ValidationError({"profesional": "Profesional inválido."})

            profesional_obj = User.objects.filter(id=profesional_id).first()
            if not profesional_obj:
                raise ValidationError({"profesional": "Profesional inválido."})

            return profesional_obj

        instance = getattr(serializer, "instance", None)
        if instance and instance.profesional_id:
            return instance.profesional

        return self.request.user

    def _resolve_times(self, data, serializer, servicio):
        instance = getattr(serializer, "instance", None)

        fecha = data.get("fecha")
        if not fecha and instance:
            fecha = instance.fecha.isoformat()

        raw_hora_inicio = data.get("hora_inicio")
        raw_hora_termina = data.get("hora_termina")

        if raw_hora_inicio in (None, "") and instance:
            raw_hora_inicio = instance.hora_inicio.strftime("%H:%M:%S")

        if raw_hora_termina in (None, "") and instance:
            raw_hora_termina = instance.hora_termina.strftime("%H:%M:%S")

        hora_inicio = _parse_time_text(raw_hora_inicio)
        hora_termina = _parse_time_text(raw_hora_termina)

        if not hora_termina and servicio and fecha and hora_inicio:
            hora_termina = _calc_hora_termina(
                fecha,
                hora_inicio.strftime("%H:%M:%S"),
                servicio.duracion,
            )

        return fecha, hora_inicio, hora_termina

    def _prepare_save_data(self, serializer):
        data = self.request.data
        instance = getattr(serializer, "instance", None)

        servicio = self._resolve_service_obj(data, serializer)
        if not servicio:
            raise ValidationError({"servicio": "Servicio inválido."})

        profesional_obj = self._resolve_profesional_obj(data, serializer)

        agenda_tipo = (data.get("agenda_tipo") or getattr(instance, "agenda_tipo", "general") or "general").strip()

        fecha, hora_inicio, hora_termina = self._resolve_times(data, serializer, servicio)

        if _exists_block_conflict(
            profesional_id=profesional_obj.id,
            fecha=fecha,
            hora_inicio=hora_inicio,
            hora_termina=hora_termina,
            exclude_id=None,
        ):
            raise ValidationError({"detail": "Ese horario está bloqueado para ese profesional."})

        return {
            "servicio": servicio,
            "profesional": profesional_obj,
            "agenda_tipo": agenda_tipo,
            "hora_inicio": hora_inicio,
            "hora_termina": hora_termina,
        }

    def perform_create(self, serializer):
        prepared = self._prepare_save_data(serializer)
        serializer.save(**prepared)

    def perform_update(self, serializer):
        prepared = self._prepare_save_data(serializer)
        serializer.save(**prepared)

    def update(self, request, *args, **kwargs):
        partial = True
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        try:
            self.perform_update(serializer)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            self.perform_destroy(instance)
        except Exception as exc:
            print("[CITAS] Error al eliminar cita:", repr(exc))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ServicioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Servicio.objects.filter(activo=True)
    serializer_class = ServicioSerializer
    permission_classes = [permissions.AllowAny]

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx


class PagoViewSet(viewsets.ModelViewSet):
    queryset = (
        Pago.objects.select_related("cita", "cita__paciente", "cita__servicio", "cita__profesional")
        .all()
        .order_by("-fecha_pago", "-id")
    )
    serializer_class = PagoSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        role = _user_role(self.request.user)
        qs = super().get_queryset()

        cita_id = self.request.query_params.get("cita")
        if cita_id:
            qs = qs.filter(cita_id=cita_id)

        profesional_id = self.request.query_params.get("profesional")
        if profesional_id:
            qs = qs.filter(cita__profesional_id=profesional_id)

        fecha_desde = self.request.query_params.get("fecha_desde")
        if fecha_desde:
            qs = qs.filter(fecha_pago__gte=fecha_desde)

        fecha_hasta = self.request.query_params.get("fecha_hasta")
        if fecha_hasta:
            qs = qs.filter(fecha_pago__lte=fecha_hasta)

        if _must_limit_to_own_records(role):
            qs = qs.filter(cita__profesional=self.request.user)

        return qs.order_by("-fecha_pago", "-id")

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="ticket", permission_classes=[IsAuthenticated])
    def ticket_pdf(self, request, pk=None):
        pago = self.get_object()
        return _build_ticket_response(pago)

    @action(detail=False, methods=["delete"], url_path=r"by-cita/(?P<cita_id>\d+)")
    def delete_by_cita(self, request, cita_id=None):
        cita = Cita.objects.filter(id=cita_id).first()
        if not cita:
            return Response(status=status.HTTP_204_NO_CONTENT)

        try:
            cita.delete()
        except Exception as exc:
            print("[PAGOS] Error borrando cita desde by-cita:", repr(exc))
            return Response({"detail": "No se pudo eliminar."}, status=400)

        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def public_agenda(request):
    fecha = request.query_params.get("fecha")
    if not fecha:
        return Response({"detail": "fecha requerida"}, status=400)

    agenda_tipo = ((request.query_params.get("agenda_tipo") or "general").strip() or "general")
    profesional_id = request.query_params.get("profesional_id") or request.query_params.get("profesional")
    profesional_nombre = (request.query_params.get("profesional_nombre") or "").strip()
    profesional_slug = (request.query_params.get("profesional_slug") or "").strip()

    clinica = _first_clinica()
    if not clinica:
        return Response({"detail": "No existe clínica configurada."}, status=400)

    profesional = _resolve_public_professional(
        clinica,
        agenda_tipo=agenda_tipo,
        profesional_id=profesional_id,
        profesional_nombre=profesional_nombre,
        profesional_slug=profesional_slug,
    )

    if not profesional:
        if agenda_tipo in ("acondicionamiento", "terapia"):
            return Response({"detail": "No se encontró configurado al fisioterapeuta Fernando."}, status=400)
        return Response({"detail": "No hay profesional configurado."}, status=400)

    qs_citas = (
        Cita.objects.filter(fecha=fecha, profesional=profesional)
        .exclude(estado="cancelado")
        .only("hora_inicio", "hora_termina", "agenda_tipo")
    )

    qs_bloq = BloqueoHorario.objects.filter(fecha=fecha, profesional=profesional).only(
        "hora_inicio",
        "hora_termina",
        "agenda_tipo",
    )

    data = []

    for cita in qs_citas:
        data.append(
            {
                "hora_inicio": cita.hora_inicio.strftime("%H:%M:%S"),
                "hora_termina": cita.hora_termina.strftime("%H:%M:%S"),
                "kind": "cita",
                "agenda_tipo": cita.agenda_tipo,
                "profesional_id": profesional.id,
                "profesional_nombre": profesional.get_full_name() or profesional.username,
            }
        )

    for bloqueo in qs_bloq:
        data.append(
            {
                "hora_inicio": bloqueo.hora_inicio.strftime("%H:%M:%S"),
                "hora_termina": bloqueo.hora_termina.strftime("%H:%M:%S"),
                "kind": "bloqueo",
                "agenda_tipo": bloqueo.agenda_tipo,
                "profesional_id": profesional.id,
                "profesional_nombre": profesional.get_full_name() or profesional.username,
            }
        )

    return Response(data)


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def public_create_cita(request):
    clinica = _first_clinica()
    if not clinica:
        return Response({"detail": "No existe clínica configurada."}, status=400)

    nombre = (request.data.get("nombre") or "").strip()
    telefono = (request.data.get("telefono") or "").strip()
    servicio_id = request.data.get("servicio_id")
    fecha = request.data.get("fecha")
    hora_inicio_raw = request.data.get("hora_inicio")

    agenda_tipo = ((request.data.get("agenda_tipo") or "general").strip() or "general")
    profesional_id = request.data.get("profesional_id") or request.data.get("profesional")
    profesional_nombre = (request.data.get("profesional_nombre") or "").strip()
    profesional_slug = (request.data.get("profesional_slug") or "").strip()
    notas = (request.data.get("notas") or "").strip()

    if not (nombre and telefono and servicio_id and fecha and hora_inicio_raw):
        return Response({"detail": "Faltan campos requeridos."}, status=400)

    servicio = Servicio.objects.filter(id=servicio_id, activo=True).first()
    if not servicio:
        return Response({"detail": "Servicio inválido."}, status=400)

    profesional = _resolve_public_professional(
        clinica,
        agenda_tipo=agenda_tipo,
        profesional_id=profesional_id,
        profesional_nombre=profesional_nombre,
        profesional_slug=profesional_slug,
    )

    if not profesional:
        if agenda_tipo in ("acondicionamiento", "terapia"):
            return Response({"detail": "No se encontró configurado al fisioterapeuta Fernando."}, status=400)
        return Response({"detail": "No hay profesional configurado."}, status=400)

    hora_inicio = _parse_time_text(hora_inicio_raw)
    if not hora_inicio:
        return Response({"detail": "Hora de inicio inválida."}, status=400)

    hora_termina = _calc_hora_termina(
        fecha,
        hora_inicio.strftime("%H:%M:%S"),
        servicio.duracion,
    )

    try:
        _validate_professional_schedule(
            profesional_id=profesional.id,
            fecha=fecha,
            hora_inicio=hora_inicio,
            hora_termina=hora_termina,
            cita_exclude_id=None,
            bloqueo_exclude_id=None,
        )
    except ValidationError as exc:
        detalle = getattr(exc, "detail", {"detail": "Horario no disponible."})
        return Response(detalle, status=409)

    paciente = Paciente.objects.create(
        clinica=clinica,
        nombres=nombre,
        apellido_pat="",
        apellido_mat="",
        telefono=telefono,
        correo="",
        genero="",
        molestia="",
        notas="",
    )
    ExpedienteClinico.objects.create(paciente=paciente)

    cita = Cita.objects.create(
        paciente=paciente,
        servicio=servicio,
        profesional=profesional,
        agenda_tipo=agenda_tipo,
        fecha=fecha,
        hora_inicio=hora_inicio,
        hora_termina=hora_termina,
        precio=servicio.precio,
        estado="reservado",
        pagado=False,
        notas=notas,
    )

    return Response(CitaSerializer(cita, context={"request": request}).data, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    user = request.user
    role = _user_role(user)
    full_name = (user.get_full_name() or "").strip() or user.username
    rol_staff = _normalize_role_value(role)

    profile, _ = StaffProfile.objects.get_or_create(
        user=user,
        defaults={
            "rol": rol_staff,
            "color_agenda": "#06b6d4",
            "cedula_profesional": "",
        },
    )

    return Response(
        {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": full_name,
            "rol": role,
            "foto_url": request.build_absolute_uri(profile.foto.url) if profile.foto else None,
            "color_agenda": _normalizar_color_hex(profile.color_agenda, "#06b6d4"),
            "cedula_profesional": profile.cedula_profesional or "",
        }
    )


class BloqueoHorarioViewSet(viewsets.ModelViewSet):
    queryset = BloqueoHorario.objects.select_related("profesional").all()
    serializer_class = BloqueoHorarioSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        role = _user_role(self.request.user)
        agenda_tipo = (self.request.query_params.get("agenda_tipo") or "").strip()
        qs = super().get_queryset()

        if agenda_tipo:
            qs = qs.filter(agenda_tipo=agenda_tipo)

            if agenda_tipo == "acondicionamiento":
                if _must_limit_to_own_records(role):
                    qs = qs.filter(profesional=self.request.user)
                return qs

            if agenda_tipo == "terapia":
                return qs

            if _must_limit_to_own_records(role):
                qs = qs.filter(profesional=self.request.user)
            return qs

        if _must_limit_to_own_records(role):
            qs = qs.filter(profesional=self.request.user)

        return qs

    def _resolve_profesional(self, data, instance=None):
        profesional_id = data.get("profesional")

        if profesional_id not in (None, "", "null"):
            profesional = User.objects.filter(id=_safe_int(profesional_id)).first()
            if not profesional:
                raise ValidationError({"profesional": "Profesional inválido."})
            return profesional

        if instance and instance.profesional_id:
            return instance.profesional

        return self.request.user

    def _resolve_block_times(self, data, instance=None):
        fecha = data.get("fecha")
        if not fecha and instance:
            fecha = instance.fecha.isoformat()

        raw_hora_inicio = data.get("hora_inicio")
        raw_hora_termina = data.get("hora_termina")

        if raw_hora_inicio in (None, "") and instance:
            raw_hora_inicio = instance.hora_inicio.strftime("%H:%M:%S")

        if raw_hora_termina in (None, "") and instance:
            raw_hora_termina = instance.hora_termina.strftime("%H:%M:%S")

        hora_inicio = _parse_time_text(raw_hora_inicio)
        hora_termina = _parse_time_text(raw_hora_termina)

        return fecha, hora_inicio, hora_termina

    def perform_create(self, serializer):
        data = self.request.data
        agenda_tipo = ((data.get("agenda_tipo") or "general").strip() or "general")
        profesional = self._resolve_profesional(data)
        fecha, hora_inicio, hora_termina = self._resolve_block_times(data)

        _validate_professional_schedule(
            profesional_id=profesional.id,
            fecha=fecha,
            hora_inicio=hora_inicio,
            hora_termina=hora_termina,
            cita_exclude_id=None,
            bloqueo_exclude_id=None,
        )

        serializer.save(
            profesional=profesional,
            agenda_tipo=agenda_tipo,
            hora_inicio=hora_inicio,
            hora_termina=hora_termina,
        )

    def perform_update(self, serializer):
        data = self.request.data
        instance = serializer.instance

        agenda_tipo = ((data.get("agenda_tipo") or instance.agenda_tipo or "general").strip() or "general")
        profesional = self._resolve_profesional(data, instance=instance)
        fecha, hora_inicio, hora_termina = self._resolve_block_times(data, instance=instance)

        _validate_professional_schedule(
            profesional_id=profesional.id,
            fecha=fecha,
            hora_inicio=hora_inicio,
            hora_termina=hora_termina,
            cita_exclude_id=None,
            bloqueo_exclude_id=instance.id,
        )

        serializer.save(
            profesional=profesional,
            agenda_tipo=agenda_tipo,
            hora_inicio=hora_inicio,
            hora_termina=hora_termina,
        )


class ServicioAdminViewSet(viewsets.ModelViewSet):
    serializer_class = ServicioSerializer
    permission_classes = [IsAdminUserStrict]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        return Servicio.objects.all().order_by("-id")

    def perform_create(self, serializer):
        clinica = _first_clinica()
        if not clinica:
            raise ValidationError({"detail": "No existe clínica configurada."})
        serializer.save(clinica=clinica)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def me_update(request):
    user = request.user

    username = (request.data.get("username") or "").strip()
    first_name = (request.data.get("first_name") or "").strip()
    last_name = (request.data.get("last_name") or "").strip()
    email = (request.data.get("email") or "").strip()

    if not username:
        return Response({"detail": "username requerido."}, status=400)
    if not email:
        return Response({"detail": "email requerido."}, status=400)

    new_password = request.data.get("new_password") or ""
    current_password = request.data.get("current_password") or ""

    if new_password:
        if not current_password:
            return Response({"detail": "Escribe tu contraseña actual para cambiarla."}, status=400)
        if not user.check_password(current_password):
            return Response({"detail": "La contraseña actual es incorrecta."}, status=400)
        if not _password_fuerte(new_password):
            return Response(
                {"detail": "La nueva contraseña no cumple requisitos (8+, mayúscula, minúscula, número, símbolo)."},
                status=400,
            )
        user.set_password(new_password)

    user.username = username
    user.first_name = first_name
    user.last_name = last_name
    user.email = email
    user.save()

    foto = request.FILES.get("foto", None)
    cedula_profesional = (request.data.get("cedula_profesional") or "").strip()
    color_agenda = _normalizar_color_hex(request.data.get("color_agenda"), "#06b6d4")

    role = _user_role(user)
    rol_staff = _normalize_role_value(role)

    profile, _ = StaffProfile.objects.get_or_create(
        user=user,
        defaults={
            "rol": rol_staff,
            "color_agenda": color_agenda,
            "cedula_profesional": cedula_profesional,
        },
    )

    if foto:
        profile.foto = foto

    if not profile.rol:
        profile.rol = rol_staff

    profile.cedula_profesional = cedula_profesional
    profile.color_agenda = color_agenda
    profile.save()

    full_name = (user.get_full_name() or "").strip() or user.username

    return Response(
        {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": full_name,
            "rol": role,
            "foto_url": request.build_absolute_uri(profile.foto.url) if profile.foto else None,
            "color_agenda": _normalizar_color_hex(profile.color_agenda, "#06b6d4"),
            "cedula_profesional": profile.cedula_profesional or "",
        }
    )

@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def password_reset_request(request):
    valor = (request.data.get("email_or_username") or "").strip()
    if not valor:
        return Response({"detail": "email_or_username requerido."}, status=400)

    user = User.objects.filter(Q(email__iexact=valor) | Q(username__iexact=valor)).first()
    ok_msg = {"detail": "Si existe el usuario, se envió el correo."}

    if not user:
        return Response(ok_msg, status=200)

    import secrets
    import string

    alphabet = string.ascii_letters + string.digits
    raw_password = "Temp-" + "".join(secrets.choice(alphabet) for _ in range(10))

    try:
        user.set_password(raw_password)
        user.save(update_fields=["password"])
    except Exception as exc:
        print("[PASSWORD RESET] Error guardando nueva contraseña:", repr(exc))
        return Response(ok_msg, status=200)

    asunto = "Recuperación de contraseña - Fisionerv"
    mensaje = (
        "Se generó una contraseña temporal para un usuario.\n\n"
        f"Usuario solicitado: {valor}\n"
        f"Usuario encontrado: {user.username}\n"
        f"Correo del usuario: {user.email or 'Sin correo registrado'}\n"
        f"Contraseña temporal: {raw_password}\n\n"
        "Comparte esta contraseña temporal con el usuario correspondiente y pídele cambiarla al iniciar sesión."
    )

    _send_email_smtp(
        asunto=asunto,
        mensaje=mensaje,
        destinatario=PASSWORD_RESET_ALERT_EMAIL,
    )

    return Response(ok_msg, status=200)

@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def password_reset_confirm(request):
    uid = request.data.get("uid") or ""
    token = request.data.get("token") or ""
    new_password = request.data.get("new_password") or ""

    if not (uid and token and new_password):
        return Response({"detail": "uid, token y new_password requeridos."}, status=400)

    if not _password_fuerte(new_password):
        return Response(
            {"detail": "La nueva contraseña no cumple requisitos (8+, mayúscula, minúscula, número, símbolo)."},
            status=400,
        )

    try:
        user_id = force_str(urlsafe_base64_decode(uid))
        user = User.objects.filter(pk=user_id).first()
    except Exception:
        user = None

    if not user:
        return Response({"detail": "Token inválido."}, status=400)

    token_gen = PasswordResetTokenGenerator()
    if not token_gen.check_token(user, token):
        return Response({"detail": "Token inválido o expirado."}, status=400)

    user.set_password(new_password)
    user.save(update_fields=["password"])

    return Response({"detail": "Contraseña actualizada correctamente."}, status=200)


class InsumoViewSet(viewsets.ModelViewSet):
    serializer_class = InsumoSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        clinica = _clinica_for_user(self.request.user)
        qs = Insumo.objects.prefetch_related("movimientos")

        q = (self.request.query_params.get("q") or "").strip()
        status_q = (self.request.query_params.get("status") or "ALL").strip().upper()
        categoria = (self.request.query_params.get("categoria") or "ALL").strip()

        if clinica:
            qs = qs.filter(clinica=clinica)

        if q:
            qs = qs.filter(
                Q(nombre__icontains=q)
                | Q(categoria__icontains=q)
                | Q(notas__icontains=q)
            )

        if categoria != "ALL":
            qs = qs.filter(categoria=categoria)

        if status_q == "LOW":
            qs = qs.filter(cantidad__lte=models.F("minimo"))
        elif status_q == "OK":
            qs = qs.filter(cantidad__gt=models.F("minimo"))

        return qs.order_by("nombre", "-id")

    def perform_create(self, serializer):
        clinica = _clinica_for_user(self.request.user) or _first_clinica()
        if not clinica:
            raise ValidationError(
                {
                    "detail": (
                        "No existe ninguna clínica registrada en la base de datos. "
                        "Crea una clínica una sola vez y después ya no necesitarás enviarla al guardar insumos."
                    )
                }
            )

        insumo = serializer.save(clinica=clinica)
        _notificar_stock_bajo_si_aplica(insumo)

    def perform_update(self, serializer):
        insumo = serializer.save()
        _notificar_stock_bajo_si_aplica(insumo)

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        clinica = _clinica_for_user(request.user)
        qs = Insumo.objects.all()
        if clinica:
            qs = qs.filter(clinica=clinica)

        total = qs.count()
        low_count = qs.filter(cantidad__lte=models.F("minimo")).count()
        meds = qs.filter(categoria="Medicamento").count()

        return Response({"total": total, "lowCount": low_count, "meds": meds})

    @action(detail=True, methods=["post"], url_path="movimiento")
    def movimiento(self, request, pk=None):
        insumo = self.get_object()

        tipo = (request.data.get("tipo") or "").strip().upper()
        cantidad = request.data.get("cantidad", 0)
        motivo = (request.data.get("motivo") or "").strip()

        try:
            cantidad = int(cantidad)
        except Exception:
            return Response({"detail": "La cantidad debe ser numérica."}, status=400)

        if tipo not in ["ENTRADA", "SALIDA", "AJUSTE"]:
            return Response({"detail": "Tipo inválido. Usa ENTRADA, SALIDA o AJUSTE."}, status=400)

        if cantidad < 0:
            return Response({"detail": "La cantidad no puede ser negativa."}, status=400)

        before = int(insumo.cantidad or 0)

        if tipo == "ENTRADA":
            after = before + cantidad
        elif tipo == "SALIDA":
            after = max(0, before - cantidad)
        else:
            after = cantidad

        movimiento = MovimientoInsumo.objects.create(
            insumo=insumo,
            tipo=tipo,
            cantidad=cantidad,
            motivo=motivo,
            before=before,
            after=after,
            creado_por=request.user,
        )

        insumo.cantidad = after
        insumo.save(update_fields=["cantidad", "actualizado"])
        _notificar_stock_bajo_si_aplica(insumo)

        return Response(
            {
                "detail": "Movimiento registrado correctamente.",
                "insumo": InsumoSerializer(insumo, context={"request": request}).data,
                "movimiento": MovimientoInsumoSerializer(movimiento).data,
            },
            status=201,
        )

    @action(detail=True, methods=["post"], url_path="inc")
    def inc(self, request, pk=None):
        insumo = self.get_object()

        delta = request.data.get("delta", 1)
        try:
            delta = int(delta)
        except Exception:
            return Response({"detail": "delta inválido."}, status=400)

        before = int(insumo.cantidad or 0)
        after = max(0, before + delta)

        tipo = "ENTRADA" if delta >= 0 else "SALIDA"
        cantidad_mov = abs(delta)

        MovimientoInsumo.objects.create(
            insumo=insumo,
            tipo=tipo,
            cantidad=cantidad_mov,
            motivo="Ajuste rápido",
            before=before,
            after=after,
            creado_por=request.user,
        )

        insumo.cantidad = after
        insumo.save(update_fields=["cantidad", "actualizado"])
        _notificar_stock_bajo_si_aplica(insumo)

        return Response(InsumoSerializer(insumo, context={"request": request}).data)

def wrap_text_by_width(pdf, text, max_width, font_name="Helvetica", font_size=8):
    text = str(text or "").strip()
    if not text:
        return []

    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        if pdf.stringWidth(test, font_name, font_size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines
        
class NotaClinicaViewSet(viewsets.ModelViewSet):
    serializer_class = NotaClinicaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = NotaClinica.objects.select_related("paciente", "cita", "sesion_clinica", "profesional")
        clinica = _clinica_for_user(self.request.user)
        if clinica:
            qs = qs.filter(paciente__clinica=clinica)

        cita_id = self.request.query_params.get("cita")
        paciente_id = self.request.query_params.get("paciente")

        if cita_id:
            qs = qs.filter(cita_id=cita_id)
        if paciente_id:
            qs = qs.filter(paciente_id=paciente_id)

        return qs.order_by("-fecha", "-id")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def _resolve_profesional(self, serializer):
        cita = serializer.validated_data.get("cita") or getattr(serializer.instance, "cita", None)
        if cita and cita.profesional_id:
            return cita.profesional
        return self.request.user

    def perform_create(self, serializer):
        serializer.save(profesional=self._resolve_profesional(serializer))

    def perform_update(self, serializer):
        serializer.save(profesional=self._resolve_profesional(serializer))

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        nota = self.get_object()
        contenido = nota.contenido_nom004 or {}
        clinica = _first_clinica()
        paciente = nota.paciente
        profesional = nota.profesional

        def full_name_user(user):
            if not user:
                return ""
            return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username

        def cedula_user(user):
            profile = getattr(user, "staff_profile", None) if user else None
            return getattr(profile, "cedula_profesional", "") or ""

        def calc_edad(fecha_nac):
            if not fecha_nac:
                return "—"
            today = timezone.localdate()
            edad = today.year - fecha_nac.year
            if (today.month, today.day) < (fecha_nac.month, fecha_nac.day):
                edad -= 1
            return str(edad)

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        azul = (0.09, 0.24, 0.45)
        azul_secundario = (0.16, 0.33, 0.58)
        azul_claro = (0.95, 0.97, 0.99)
        gris = (0.20, 0.24, 0.30)
        borde = (0.82, 0.87, 0.93)

        margin_x = 36
        y = height - 38
        logo_path = _media_file_path("logo.png")

        def new_page():
            nonlocal y
            pdf.showPage()
            y = height - 38
            draw_header()

        def draw_header():
            nonlocal y

            header_h = 64
            pdf.setFillColorRGB(*azul)
            pdf.roundRect(
                margin_x,
                y - header_h,
                width - (margin_x * 2),
                header_h,
                14,
                fill=1,
                stroke=0,
            )

            if logo_path:
                try:
                    pdf.setFillColorRGB(1, 1, 1)
                    pdf.roundRect(margin_x + 12, y - 50, 112, 34, 10, fill=1, stroke=0)
                    pdf.drawImage(
                        ImageReader(logo_path),
                        margin_x + 18,
                        y - 46,
                        width=96,
                        height=26,
                        preserveAspectRatio=True,
                        mask="auto",
                    )
                except Exception:
                    pass

            clinic_name = (getattr(clinica, "nombre", "") or "ORTHO CLINIC").upper()
            clinic_dir = (getattr(clinica, "direccion", "") or "").upper()

            right_x = width - margin_x - 16
            max_text_width = 210

            pdf.setFillColorRGB(1, 1, 1)
            pdf.setFont("Helvetica-Bold", 17)
            pdf.drawRightString(right_x, y - 20, "NOTA CLÍNICA")

            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawRightString(right_x, y - 35, clinic_name)

            dir_lines = wrap_text_by_width(
                pdf,
                clinic_dir,
                max_width=max_text_width,
                font_name="Helvetica",
                font_size=7.5,
            )

            pdf.setFont("Helvetica", 7.5)
            text_y = y - 47
            for line in dir_lines[:2]:
                pdf.drawRightString(right_x, text_y, line)
                text_y -= 9

            y -= 78

        def draw_patient_box():
            nonlocal y

            box_h = 62
            pdf.setFillColorRGB(*azul_claro)
            pdf.roundRect(
                margin_x,
                y - box_h,
                width - (margin_x * 2),
                box_h,
                10,
                fill=1,
                stroke=0,
            )

            paciente_nombre = f"{paciente.nombres} {paciente.apellido_pat} {paciente.apellido_mat or ''}".strip()
            profesion = full_name_user(profesional) or "PROFESIONAL NO ASIGNADO"
            cedula = cedula_user(profesional) or "—"

            col1_x = margin_x + 14
            col2_x = width / 2 + 10

            pdf.setFillColorRGB(*gris)
            pdf.setFont("Helvetica-Bold", 8.5)
            pdf.drawString(col1_x, y - 18, "PACIENTE")
            pdf.drawString(col1_x, y - 34, "FECHA")
            pdf.drawString(col1_x, y - 50, "EDAD")

            pdf.drawString(col2_x, y - 18, "PROFESIONAL")
            pdf.drawString(col2_x, y - 34, "TIPO DE NOTA")
            pdf.drawString(col2_x, y - 50, "CÉDULA")

            pdf.setFont("Helvetica", 8.5)
            pdf.drawString(col1_x + 72, y - 18, paciente_nombre[:42])
            pdf.drawString(col1_x + 72, y - 34, str(nota.fecha))
            pdf.drawString(col1_x + 72, y - 50, calc_edad(paciente.fecha_nac))

            pdf.drawString(col2_x + 84, y - 18, profesion[:32])
            pdf.drawString(col2_x + 84, y - 34, (nota.get_tipo_nota_display() or "").upper()[:28])
            pdf.drawString(col2_x + 84, y - 50, str(cedula))

            y -= 76

        def draw_section(title, value):
            nonlocal y

            texto = str(value or "").strip() or "—"
            wrapped_text = []

            for paragraph in texto.splitlines() or ["—"]:
                wrapped = wrap_text_by_width(
                    pdf,
                    paragraph,
                    max_width=width - (margin_x * 2) - 24,
                    font_name="Helvetica",
                    font_size=8.5,
                )
                if wrapped:
                    wrapped_text.extend(wrapped)
                else:
                    wrapped_text.append(" ")

            required_height = 24 + max(40, len(wrapped_text) * 11 + 14)
            if y - required_height < 70:
                new_page()

            pdf.setFillColorRGB(*azul_secundario)
            pdf.roundRect(
                margin_x,
                y - 16,
                width - (margin_x * 2),
                16,
                7,
                fill=1,
                stroke=0,
            )

            pdf.setFillColorRGB(1, 1, 1)
            pdf.setFont("Helvetica-Bold", 8.8)
            pdf.drawString(margin_x + 10, y - 11, str(title).upper())

            y -= 24

            box_height = max(40, len(wrapped_text) * 11 + 14)
            pdf.setStrokeColorRGB(*borde)
            pdf.setFillColorRGB(1, 1, 1)
            pdf.roundRect(
                margin_x,
                y - box_height,
                width - (margin_x * 2),
                box_height,
                7,
                fill=0,
                stroke=1,
            )

            pdf.setFillColorRGB(*gris)
            pdf.setFont("Helvetica", 8.5)
            current_y = y - 12
            for line in wrapped_text:
                pdf.drawString(margin_x + 10, current_y, line)
                current_y -= 11

            y -= box_height + 10

        def draw_footer():
            firma_nombre = (full_name_user(profesional) or "PROFESIONAL NO ASIGNADO").upper()
            cedula = cedula_user(profesional)

            if y < 100:
                new_page()

            footer_y = 56
            pdf.setStrokeColorRGB(*azul_secundario)
            pdf.line(width - 220, footer_y + 16, width - 40, footer_y + 16)

            pdf.setFont("Helvetica-Bold", 9)
            pdf.setFillColorRGB(*gris)
            pdf.drawString(width - 215, footer_y + 2, firma_nombre[:38])

            if cedula:
                pdf.setFont("Helvetica", 8)
                pdf.drawString(width - 215, footer_y - 10, f"CÉDULA PROFESIONAL: {cedula}")

        campos_por_tipo = {
            "historia_clinica": [
                ("Ficha de identificación", contenido.get("ficha_identificacion")),
                ("Grupo étnico", contenido.get("grupo_etnico")),
                ("Antecedentes heredo-familiares", contenido.get("antecedentes_heredo_familiares")),
                ("Antecedentes personales patológicos", contenido.get("antecedentes_personales_patologicos")),
                ("Antecedentes personales no patológicos", contenido.get("antecedentes_personales_no_patologicos")),
                ("Uso y dependencia de sustancias", contenido.get("consumo_sustancias_psicoactivas")),
                ("Padecimiento actual", contenido.get("padecimiento_actual")),
                ("Interrogatorio por aparatos y sistemas", contenido.get("interrogatorio_aparatos_sistemas")),
                ("Habitus exterior", contenido.get("habitus_exterior")),
                ("Signos vitales", contenido.get("signos_vitales")),
                ("Peso y talla", contenido.get("peso_talla")),
                ("Exploración física", contenido.get("exploracion_fisica")),
                ("Resultados de estudios", contenido.get("resultados_estudios")),
                ("Diagnósticos o problemas clínicos", contenido.get("diagnosticos_problemas")),
                ("Pronóstico", contenido.get("pronostico")),
                ("Indicación terapéutica", contenido.get("indicacion_terapeutica")),
            ],
            "evolucion": [
                ("Evolución y actualización del cuadro clínico", contenido.get("evolucion_cuadro_clinico")),
                ("Signos vitales", contenido.get("signos_vitales")),
                ("Resultados relevantes", contenido.get("resultados_relevantes")),
                ("Diagnósticos o problemas clínicos", contenido.get("diagnosticos_problemas")),
                ("Pronóstico", contenido.get("pronostico")),
                ("Tratamiento e indicaciones médicas", contenido.get("tratamiento_indicaciones")),
            ],
            "interconsulta": [
                ("Criterios diagnósticos", contenido.get("criterios_diagnosticos")),
                ("Plan de estudios", contenido.get("plan_estudios")),
                ("Sugerencias diagnósticas y tratamiento", contenido.get("sugerencias_diagnosticas_tratamiento")),
                ("Notas complementarias", contenido.get("notas_complementarias")),
            ],
            "referencia_traslado": [
                ("Establecimiento que envía", contenido.get("establecimiento_envia")),
                ("Establecimiento receptor", contenido.get("establecimiento_receptor")),
                ("Motivo de envío", contenido.get("motivo_envio")),
                ("Impresión diagnóstica", contenido.get("impresion_diagnostica")),
                ("Terapéutica empleada", contenido.get("terapeutica_empleada")),
                ("Resumen clínico", contenido.get("resumen_clinico")),
            ],
        }

        secciones = campos_por_tipo.get(nota.tipo_nota) or [
            ("Subjetivo", nota.subjetivo),
            ("Objetivo", nota.objetivo),
            ("Análisis", nota.analisis),
            ("Plan", nota.plan),
            ("Observaciones", nota.observaciones),
        ]

        draw_header()
        draw_patient_box()

        for titulo, valor in secciones:
            draw_section(titulo, valor)

        draw_footer()
        pdf.showPage()
        pdf.save()

        pdf_bytes = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        inline = str(request.query_params.get("inline") or "").lower() in ("1", "true", "yes")
        disposition = "inline" if inline else "attachment"
        response["Content-Disposition"] = f'{disposition}; filename="nota_clinica_{nota.id}.pdf"'
        return response

class RecetaMedicaViewSet(viewsets.ModelViewSet):
    serializer_class = RecetaMedicaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = RecetaMedica.objects.select_related("paciente", "cita", "profesional")
        clinica = _clinica_for_user(self.request.user)

        if clinica:
            qs = qs.filter(paciente__clinica=clinica)

        cita_id = self.request.query_params.get("cita")
        paciente_id = self.request.query_params.get("paciente")

        if cita_id:
            qs = qs.filter(cita_id=cita_id)

        if paciente_id:
            qs = qs.filter(paciente_id=paciente_id)

        return qs.order_by("-fecha", "-id")

    def _resolve_profesional(self, serializer):
        cita = serializer.validated_data.get("cita") or getattr(serializer.instance, "cita", None)
        if cita and cita.profesional_id:
            return cita.profesional
        return self.request.user

    def perform_create(self, serializer):
        serializer.save(profesional=self._resolve_profesional(serializer))

    def perform_update(self, serializer):
        serializer.save(profesional=self._resolve_profesional(serializer))

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        receta = self.get_object()
        paciente = receta.paciente
        profesional = receta.profesional
        clinica = _first_clinica()

        def full_name_user(user):
            if not user:
                return ""
            return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username

        def cedula_user(user):
            profile = getattr(user, "staff_profile", None) if user else None
            return getattr(profile, "cedula_profesional", "") or ""

        def calc_edad(fecha_nac):
            if not fecha_nac:
                return "-"
            today = timezone.localdate()
            edad = today.year - fecha_nac.year
            if (today.month, today.day) < (fecha_nac.month, fecha_nac.day):
                edad -= 1
            return str(edad)

        def to_text(value, fallback="-"):
            value = str(value or "").strip()
            return value if value else fallback

        def wrap_lines(texto, width=110):
            salida = []
            for paragraph in str(texto or "").splitlines() or [""]:
                lineas = textwrap.wrap(
                    paragraph,
                    width=width,
                    break_long_words=True,
                    break_on_hyphens=False,
                )
                salida.extend(lineas or [" "])
            return salida

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=landscape(letter))
        width, height = landscape(letter)

        azul = (0.09, 0.24, 0.45)
        azul_secundario = (0.16, 0.33, 0.58)
        azul_claro = (0.95, 0.97, 0.99)
        gris = (0.20, 0.24, 0.30)
        borde = (0.82, 0.87, 0.93)

        margin_x = 34
        y = height - 40
        logo_path = _media_file_path("logo.png")

        def new_page():
            nonlocal y
            pdf.showPage()
            y = height - 34
            draw_header()

        def draw_header():
            nonlocal y

            header_h = 68
            pdf.setFillColorRGB(*azul)
            pdf.roundRect(
                margin_x,
                y - header_h,
                width - (margin_x * 2),
                header_h,
                14,
                fill=1,
                stroke=0,
            )

            if logo_path:
                try:
                    pdf.setFillColorRGB(1, 1, 1)
                    pdf.roundRect(margin_x + 12, y - 56, 120, 42, 9, fill=1, stroke=0)
                    pdf.drawImage(
                        ImageReader(logo_path),
                        margin_x + 18,
                        y - 54,
                        width=104,
                        height=40,
                        preserveAspectRatio=True,
                        mask="auto",
                    )
                except Exception:
                    pass

            doctor_name = to_text(full_name_user(profesional), "MEDICO TRATANTE").upper()
            clinic_name = to_text(getattr(clinica, "nombre", ""), "ORTHO CLINIC").upper()
            clinic_dir = to_text(getattr(clinica, "direccion", ""), "").upper()

            right_x = width - margin_x - 16

            pdf.setFillColorRGB(1, 1, 1)
            pdf.setFont("Helvetica-Bold", 15)
            pdf.drawRightString(right_x, y - 20, doctor_name[:42])

            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawRightString(right_x, y - 33, "RECETA MEDICA")

            dir_lines = wrap_text_by_width(
                pdf,
                f"{clinic_name} · {clinic_dir}".strip(" ·"),
                max_width=285,
                font_name="Helvetica",
                font_size=7.5,
            )

            pdf.setFont("Helvetica", 7.5)
            text_y = y - 45
            for line in dir_lines[:2]:
                pdf.drawRightString(right_x, text_y, line)
                text_y -= 8

            y -= 70

        def draw_patient_box():
            nonlocal y

            box_h = 48
            pdf.setFillColorRGB(*azul_claro)
            pdf.roundRect(
                margin_x,
                y - box_h,
                width - (margin_x * 2),
                box_h,
                10,
                fill=1,
                stroke=0,
            )

            paciente_nombre = f"{paciente.nombres} {paciente.apellido_pat} {paciente.apellido_mat or ''}".strip()

            pdf.setFillColorRGB(*gris)
            pdf.setFont("Helvetica-Bold", 8.5)
            pdf.drawString(margin_x + 12, y - 16, "PACIENTE")
            pdf.drawString(margin_x + 12, y - 32, "FECHA")
            pdf.drawString(margin_x + 290, y - 16, "EDAD")
            pdf.drawString(margin_x + 290, y - 32, "FECHA DE NACIMIENTO")

            pdf.setFont("Helvetica", 8.5)
            pdf.drawString(margin_x + 78, y - 16, paciente_nombre[:50])
            pdf.drawString(margin_x + 78, y - 32, str(receta.fecha or "-"))
            pdf.drawString(margin_x + 332, y - 16, calc_edad(paciente.fecha_nac))
            pdf.drawString(margin_x + 398, y - 32, str(paciente.fecha_nac or "-"))

            y -= 58

        def draw_section(title, lines, min_height=40):
            nonlocal y

            lines = lines or ["-"]
            expanded_lines = []

            for line in lines:
                wrapped = wrap_text_by_width(
                    pdf,
                    str(line or ""),
                    max_width=width - (margin_x * 2) - 24,
                    font_name="Helvetica",
                    font_size=8.5,
                )
                expanded_lines.extend(wrapped or [" "])

            if not expanded_lines:
                expanded_lines = ["-"]

            pending = expanded_lines[:]
            first_block = True

            while pending:
                if y < 120:
                    new_page()

                available_height = y - 92
                max_lines_fit = max(1, int((available_height - 24 - 14) / 10))

                current_block = pending[:max_lines_fit]
                pending = pending[max_lines_fit:]

                current_title = title if first_block else f"{title} (CONT.)"
                current_min_height = min_height if first_block and not pending else 0

                pdf.setFillColorRGB(*azul_secundario)
                pdf.roundRect(
                    margin_x,
                    y - 16,
                    width - (margin_x * 2),
                    16,
                    7,
                    fill=1,
                    stroke=0,
                )

                pdf.setFillColorRGB(1, 1, 1)
                pdf.setFont("Helvetica-Bold", 8.8)
                pdf.drawString(margin_x + 10, y - 11, str(current_title).upper())

                y -= 24

                box_height = max(current_min_height, len(current_block) * 10 + 14)
                pdf.setStrokeColorRGB(*borde)
                pdf.setFillColorRGB(1, 1, 1)
                pdf.roundRect(
                    margin_x,
                    y - box_height,
                    width - (margin_x * 2),
                    box_height,
                    7,
                    fill=0,
                    stroke=1,
                )

                pdf.setFillColorRGB(*gris)
                pdf.setFont("Helvetica", 8.5)
                current_y = y - 12

                for line in current_block:
                    pdf.drawString(margin_x + 10, current_y, str(line))
                    current_y -= 10

                y -= box_height + 10
                first_block = False

        def draw_signature():
            nonlocal y

            firma_nombre = to_text(full_name_user(profesional), "DOCTOR NO ASIGNADO").upper()
            cedula = cedula_user(profesional)

            if y < 110:
                new_page()

            firma_x1 = width - 250
            firma_x2 = width - 70
            firma_y = 72

            pdf.setStrokeColorRGB(*azul_secundario)
            pdf.line(firma_x1, firma_y, firma_x2, firma_y)

            pdf.setFillColorRGB(*gris)
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawCentredString((firma_x1 + firma_x2) / 2, firma_y - 12, firma_nombre[:42])

            if cedula:
                pdf.setFont("Helvetica", 8)
                pdf.drawCentredString(
                    (firma_x1 + firma_x2) / 2,
                    firma_y - 24,
                    f"CEDULA PROFESIONAL: {cedula}",
                )

        draw_header()
        draw_patient_box()

        medicamentos = receta.medicamentos if isinstance(receta.medicamentos, list) else []
        meds_lines = []

        if medicamentos:
            for idx, med in enumerate(medicamentos, start=1):
                nombre = to_text(med.get("nombre"), "MEDICAMENTO")
                dosis = to_text(med.get("dosis"))
                via = to_text(med.get("via_administracion"))
                frecuencia = to_text(med.get("frecuencia"))
                duracion = to_text(med.get("duracion"))
                notas = to_text(med.get("notas"), "")

                texto = (
                    f"{idx}. {nombre.upper()} | "
                    f"DOSIS: {dosis.upper()} | "
                    f"VIA: {via.upper()} | "
                    f"FRECUENCIA: {frecuencia.upper()} | "
                    f"DURACION: {duracion.upper()}"
                )

                if notas:
                    texto += f" | NOTAS: {notas.upper()}"

                meds_lines.extend(wrap_lines(texto, width=108))
        else:
            meds_lines = ["1. SIN MEDICAMENTOS CAPTURADOS."]

        diagnostico_lines = wrap_lines(to_text(receta.diagnostico).upper(), width=108)
        indicaciones_lines = wrap_lines(to_text(receta.indicaciones_generales).upper(), width=108)

        draw_section("Medicamentos", meds_lines, min_height=76)
        draw_section("Diagnostico", diagnostico_lines, min_height=50)
        draw_section("Indicaciones generales", indicaciones_lines, min_height=68)
        draw_signature()

        pdf.save()

        pdf_bytes = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        inline = str(request.query_params.get("inline") or "").lower() in ("1", "true", "yes")
        disposition = "inline" if inline else "attachment"
        response["Content-Disposition"] = f'{disposition}; filename="receta_medica_{receta.id}.pdf"'
        return response

class EvidenciaClinicaViewSet(viewsets.ModelViewSet):
    serializer_class = EvidenciaClinicaSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        qs = EvidenciaClinica.objects.select_related("paciente", "cita", "sesion_clinica", "subido_por")
        clinica = _clinica_for_user(self.request.user)
        if clinica:
            qs = qs.filter(paciente__clinica=clinica)

        cita_id = self.request.query_params.get("cita")
        paciente_id = self.request.query_params.get("paciente")

        if cita_id:
            qs = qs.filter(cita_id=cita_id)
        if paciente_id:
            qs = qs.filter(paciente_id=paciente_id)

        return qs.order_by("-creado", "-id")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def perform_create(self, serializer):
        archivo = self.request.FILES.get("archivo")
        tipo = _tipo_archivo_desde_nombre(getattr(archivo, "name", ""))
        serializer.save(subido_por=self.request.user, tipo_archivo=tipo)
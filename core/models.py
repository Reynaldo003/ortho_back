# core/models.py
from django.conf import settings
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.validators import FileExtensionValidator
from datetime import date
class StaffProfile(models.Model):
    ROLE_CHOICES = [
        ("doctor", "Doctor"),
        ("fisioterapeuta", "Fisioterapeuta"),
        ("aux_fisioterapia", "Auxiliar de fisioterapia"),
        ("recepcionista", "Recepcionista"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="staff_profile")
    rol = models.CharField(max_length=30, choices=ROLE_CHOICES, default="recepcionista")
    telefono = models.CharField(max_length=30, blank=True, default="")
    descripcion = models.TextField(blank=True, default="")
    foto = models.ImageField(upload_to="staff/", blank=True, null=True)
    cedula_profesional = models.CharField(max_length=30, blank=True, default="")

    color_agenda = models.CharField(max_length=20, blank=True, default="#d47b06")

    creado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.rol})"

class Clinica(models.Model):
    nombre = models.CharField(max_length=100)
    direccion = models.CharField(max_length=255)
    propietario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="clinicas",
    )

    def __str__(self):
        return self.nombre


class PerfilUsuario(models.Model):
    ROLES = [
        ("doctor", "Doctor"),
        ("fisioterapeuta", "Fisioterapeuta"),
        ("aux_fisioterapia", "Auxiliar de fisioterapia"),
        ("recepcionista", "Recepcionista"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil",
    )
    clinica = models.ForeignKey(
        Clinica,
        on_delete=models.CASCADE,
        related_name="perfiles",
        null=True,
        blank=True,
    )
    rol = models.CharField(max_length=30, choices=ROLES, default="recepcionista")

    titulo = models.CharField(max_length=30, blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    foto = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.rol})"


class HorarioDisponible(models.Model):
    DIA_SEMANA = [
        (0, "Lunes"),
        (1, "Martes"),
        (2, "Miércoles"),
        (3, "Jueves"),
        (4, "Viernes"),
        (5, "Sábado"),
        (6, "Domingo"),
    ]

    clinica = models.ForeignKey(
        Clinica,
        on_delete=models.CASCADE,
        related_name="horarios",
    )
    dia = models.PositiveSmallIntegerField(choices=DIA_SEMANA)
    hora_apertura = models.TimeField()
    hora_cierre = models.TimeField()

    class Meta:
        unique_together = ("clinica", "dia")

    def __str__(self):
        return f"{self.get_dia_display()} {self.hora_apertura}-{self.hora_cierre}"


class Servicio(models.Model):
    clinica = models.ForeignKey(
        Clinica,
        on_delete=models.CASCADE,
        related_name="servicios",
    )
    nombre = models.CharField(max_length=100)
    descripcion = models.CharField(max_length=150)
    duracion = models.DurationField()
    precio = models.DecimalField(max_digits=8, decimal_places=2)
    activo = models.BooleanField(default=True)
    imagen = models.ImageField(upload_to="servicios/", blank=True, null=True)

    def __str__(self):
        return f"{self.nombre} ({self.clinica.nombre})"

class Paciente(models.Model):
    ESTADO_TRATAMIENTO = [
        ("en_tratamiento", "En tratamiento"),
        ("alta", "Dado de alta"),
    ]

    clinica = models.ForeignKey(
        "Clinica",
        on_delete=models.CASCADE,
        related_name="pacientes",
    )
    nombres = models.CharField(max_length=70)
    apellido_pat = models.CharField(max_length=40)
    apellido_mat = models.CharField(max_length=40, blank=True)
    fecha_nac = models.DateField(null=True, blank=True)
    genero = models.CharField(max_length=30, blank=True)

    # OJO:
    # el teléfono NO debe ser único.
    telefono = models.CharField(max_length=20, blank=True, default="")
    correo = models.EmailField(max_length=100, blank=True)

    molestia = models.CharField(max_length=150, blank=True)
    notas = models.TextField(blank=True, default="")
    registro = models.DateField(auto_now_add=True)

    estado_tratamiento = models.CharField(
        max_length=20,
        choices=ESTADO_TRATAMIENTO,
        default="en_tratamiento",
    )
    fecha_alta = models.DateField(null=True, blank=True)

    # =========================
    # Facturación MX
    # =========================
    requiere_factura = models.BooleanField(default=False)
    facturacion_razon_social = models.CharField(max_length=180, blank=True, default="")
    facturacion_rfc = models.CharField(max_length=13, blank=True, default="")
    facturacion_regimen_fiscal = models.CharField(max_length=80, blank=True, default="")
    facturacion_codigo_postal = models.CharField(max_length=10, blank=True, default="")
    facturacion_uso_cfdi = models.CharField(max_length=30, blank=True, default="")
    facturacion_correo = models.EmailField(blank=True, default="")

    class Meta:
        ordering = ["nombres", "apellido_pat", "apellido_mat", "-id"]
        indexes = [
            models.Index(fields=["clinica", "telefono"]),
            models.Index(fields=["clinica", "correo"]),
            models.Index(fields=["clinica", "apellido_pat", "apellido_mat"]),
            models.Index(fields=["estado_tratamiento"]),
        ]

    def __str__(self):
        return f"{self.nombres} {self.apellido_pat}".strip()


class NotaClinica(models.Model):
    TIPOS_NOTA = [
        ("historia_clinica", "Historia clínica"),
        ("evolucion", "Nota de evolución"),
        ("interconsulta", "Nota de interconsulta"),
        ("referencia_traslado", "Nota de referencia / traslado"),
    ]

    paciente = models.ForeignKey(
        Paciente,
        on_delete=models.CASCADE,
        related_name="notas_clinicas",
    )
    cita = models.OneToOneField(
        "Cita",
        on_delete=models.CASCADE,
        related_name="nota_clinica",
        null=True,
        blank=True,
    )
    sesion_clinica = models.OneToOneField(
        "SesionClinica",
        on_delete=models.SET_NULL,
        related_name="nota_clinica",
        null=True,
        blank=True,
    )
    profesional = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notas_clinicas_realizadas",
    )

    fecha = models.DateField(default=date.today)
    tipo_nota = models.CharField(max_length=30, choices=TIPOS_NOTA, default="evolucion")
    contenido_nom004 = models.JSONField(default=dict, blank=True)
    subjetivo = models.TextField(blank=True, default="")
    objetivo = models.TextField(blank=True, default="")
    analisis = models.TextField(blank=True, default="")
    plan = models.TextField(blank=True, default="")
    observaciones = models.TextField(blank=True, default="")

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["paciente", "fecha"]),
        ]

    def __str__(self):
        return f"Nota clínica {self.paciente_id} - {self.fecha}"


class RecetaMedica(models.Model):
    paciente = models.ForeignKey(
        Paciente,
        on_delete=models.CASCADE,
        related_name="recetas_medicas",
    )
    cita = models.OneToOneField(
        "Cita",
        on_delete=models.CASCADE,
        related_name="receta_medica",
    )
    profesional = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recetas_medicas_realizadas",
    )

    fecha = models.DateField(default=date.today)
    diagnostico = models.TextField(blank=True, default="")
    indicaciones_generales = models.TextField(blank=True, default="")
    medicamentos = models.JSONField(default=list, blank=True)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha", "-id"]

    def __str__(self):
        return f"Receta cita {self.cita_id}"


class EvidenciaClinica(models.Model):
    TIPOS_ARCHIVO = [
        ("imagen", "Imagen"),
        ("pdf", "PDF"),
        ("otro", "Otro"),
    ]

    paciente = models.ForeignKey(
        Paciente,
        on_delete=models.CASCADE,
        related_name="evidencias_clinicas",
    )
    cita = models.ForeignKey(
        "Cita",
        on_delete=models.CASCADE,
        related_name="evidencias_clinicas",
    )
    sesion_clinica = models.ForeignKey(
        "SesionClinica",
        on_delete=models.SET_NULL,
        related_name="evidencias_clinicas",
        null=True,
        blank=True,
    )
    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evidencias_clinicas_subidas",
    )

    titulo = models.CharField(max_length=180, blank=True, default="")
    descripcion = models.TextField(blank=True, default="")
    tipo_archivo = models.CharField(max_length=10, choices=TIPOS_ARCHIVO, default="otro")
    archivo = models.FileField(
        upload_to="evidencias_clinicas/%Y/%m/",
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp", "pdf"])],
    )

    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado", "-id"]
        indexes = [
            models.Index(fields=["paciente", "cita"]),
        ]

    def __str__(self):
        return f"Evidencia {self.paciente_id} - {self.cita_id}"

class ExpedienteClinico(models.Model):
    paciente = models.OneToOneField(
        Paciente,
        on_delete=models.CASCADE,
        related_name="expediente",
    )

    ocupacion = models.CharField(max_length=120, blank=True, default="")
    direccion = models.TextField(blank=True, default="")
    heredo_familiares = models.TextField(blank=True, default="")

    antecedentes = models.JSONField(default=dict, blank=True)
    habitos = models.JSONField(default=dict, blank=True)
    documentos = models.JSONField(default=dict, blank=True)

    notas_generales = models.TextField(blank=True, default="")

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Expediente #{self.pk} - Paciente {self.paciente_id}"


class SesionClinica(models.Model):
    ESTADOS_SESION = [
        ("estable", "Estable"),
        ("mejorando", "Mejorando"),
        ("igual", "Sin cambios"),
        ("empeorando", "Empeorando"),
        ("alta", "Alta"),
    ]

    paciente = models.ForeignKey(
        Paciente,
        on_delete=models.CASCADE,
        related_name="sesiones_clinicas",
    )
    cita = models.ForeignKey(
        "Cita",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sesiones_clinicas",
    )
    profesional = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sesiones_clinicas_realizadas",
    )

    fecha = models.DateField()
    motivo_consulta = models.CharField(max_length=200, blank=True, default="")
    intensidad_dolor = models.PositiveSmallIntegerField(null=True, blank=True)
    zonas_dolor = models.JSONField(default=list, blank=True)

    notas = models.TextField(blank=True, default="")
    exploracion = models.TextField(blank=True, default="")
    diagnostico = models.TextField(blank=True, default="")
    tratamiento_realizado = models.TextField(blank=True, default="")
    recomendaciones = models.TextField(blank=True, default="")

    estado_sesion = models.CharField(
        max_length=20,
        choices=ESTADOS_SESION,
        blank=True,
        default="",
    )

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["paciente", "fecha"]),
            models.Index(fields=["profesional", "fecha"]),
        ]

    def __str__(self):
        return f"Sesión {self.paciente_id} - {self.fecha}"


class Comentario(models.Model):
    TIPOS_OBJETIVO = [
        ("profesional", "Profesional"),
        ("servicio", "Servicio"),
    ]

    clinica = models.ForeignKey(
        Clinica,
        on_delete=models.CASCADE,
        related_name="comentarios",
    )

    tipo_objetivo = models.CharField(
        max_length=20,
        choices=TIPOS_OBJETIVO,
        db_index=True,
        default=""
    )

    profesional = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="comentarios_recibidos",
    )

    servicio = models.ForeignKey(
        "Servicio",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="comentarios_recibidos",
    )

    descripcion = models.TextField(max_length=300)
    calificacion = models.PositiveSmallIntegerField()
    aprobado = models.BooleanField(default=False, db_index=True)
    nombre_completo = models.CharField(
        max_length=100,
        blank=True,
        default="Paciente anónimo",
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado", "-id"]
        indexes = [
            models.Index(fields=["aprobado", "creado"]),
            models.Index(fields=["tipo_objetivo", "profesional"]),
            models.Index(fields=["tipo_objetivo", "servicio"]),
        ]

    def __str__(self):
        objetivo = ""
        if self.tipo_objetivo == "profesional" and self.profesional:
            objetivo = self.profesional.get_full_name() or self.profesional.username
        elif self.tipo_objetivo == "servicio" and self.servicio:
            objetivo = self.servicio.nombre
        else:
            objetivo = "Sin objetivo"

        return f"{self.nombre_completo} ({self.calificacion}) - {objetivo}"

from django.conf import settings
from django.db import models

class Cita(models.Model):
    ESTADOS = [
        ("reservado", "Reservado"),
        ("confirmado", "Confirmado"),
        ("completado", "Completado"),
        ("cancelado", "Cancelado"),
    ]

    METODOS_PAGO = [
        ("efectivo", "Efectivo"),
        ("tarjeta", "Tarjeta"),
        ("transferencia", "Transferencia"),
        ("otro", "Otro"),
    ]

    AGENDA_TIPOS = [
        ("general", "General"),
        ("acondicionamiento", "Acondicionamiento"),
        ("terapia", "Terapia"),
    ]

    paciente = models.ForeignKey(
        "Paciente",
        on_delete=models.CASCADE,
        related_name="citas",
    )
    servicio = models.ForeignKey(
        "Servicio",
        on_delete=models.PROTECT,
        related_name="citas",
    )
    profesional = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="citas_atendidas",
    )

    agenda_tipo = models.CharField(
        max_length=30,
        choices=AGENDA_TIPOS,
        default="general",
        db_index=True,
    )

    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_termina = models.TimeField()

    precio = models.DecimalField(max_digits=8, decimal_places=2)
    pagado = models.BooleanField(default=False)

    metodo_pago = models.CharField(
        max_length=20,
        choices=METODOS_PAGO,
        blank=True,
    )
    descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )
    anticipo = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text="Suma de todos los abonos registrados.",
    )
    monto_final = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text="Total de la cita después de descuentos.",
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default="reservado",
    )
    notas = models.CharField(max_length=200, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha", "-hora_inicio"]
        indexes = [
            models.Index(fields=["agenda_tipo", "fecha"]),
            models.Index(fields=["agenda_tipo", "profesional", "fecha"]),
        ]

    def __str__(self):
        return f"{self.paciente} - {self.fecha} {self.hora_inicio} ({self.agenda_tipo})"

class Pago(models.Model):
    cita = models.ForeignKey(
        Cita,
        on_delete=models.CASCADE,
        related_name="pagos",
    )
    fecha_pago = models.DateField()
    comprobante = models.CharField(max_length=100, blank=True)
    monto_facturado = models.DecimalField(max_digits=8, decimal_places=2)
    metodo_pago = models.CharField(
        max_length=20,
        choices=Cita.METODOS_PAGO,
    )
    descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )
    anticipo = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
    )
    restante = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_pago", "-id"]

    def __str__(self):
        return f"Pago #{self.id} - Cita {self.cita_id}"



class BloqueoHorario(models.Model):
    AGENDA_TIPOS = [
        ("general", "General"),
        ("acondicionamiento", "Acondicionamiento"),
        ("terapia", "Terapia"),
    ]

    profesional = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bloqueos_horario",
    )
    agenda_tipo = models.CharField(
        max_length=30,
        choices=AGENDA_TIPOS,
        default="general",
        db_index=True,
    )
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_termina = models.TimeField()
    motivo = models.CharField(max_length=200, blank=True, default="")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-hora_inicio"]
        indexes = [
            models.Index(fields=["agenda_tipo", "fecha"]),
            models.Index(fields=["agenda_tipo", "profesional", "fecha"]),
        ]

    def __str__(self):
        return f"Bloqueo {self.fecha} {self.hora_inicio}-{self.hora_termina} ({self.profesional_id}) [{self.agenda_tipo}]"

class Insumo(models.Model):
    CATEGORIAS = [
        ("Insumo", "Insumo"),
        ("Medicamento", "Medicamento"),
    ]

    clinica = models.ForeignKey(
        Clinica,
        on_delete=models.CASCADE,
        related_name="insumos",
    )
    nombre = models.CharField(max_length=120)
    categoria = models.CharField(max_length=20, choices=CATEGORIAS, default="Insumo")
    cantidad = models.PositiveIntegerField(default=0)
    minimo = models.PositiveIntegerField(default=0)
    notas = models.CharField(max_length=250, blank=True, default="")

    # ✅ evita enviar el mismo correo muchas veces mientras siga en bajo stock
    alerta_stock_bajo_enviada = models.BooleanField(default=False)
    alerta_stock_bajo_fecha = models.DateTimeField(null=True, blank=True)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre", "-id"]

    def __str__(self):
        return f"{self.nombre} ({self.cantidad})"

class MovimientoInsumo(models.Model):
    TIPOS = [
        ("ENTRADA", "Entrada"),
        ("SALIDA", "Salida"),
        ("AJUSTE", "Ajuste"),
    ]

    insumo = models.ForeignKey(
        Insumo,
        on_delete=models.CASCADE,
        related_name="movimientos",
    )
    tipo = models.CharField(max_length=10, choices=TIPOS)
    cantidad = models.PositiveIntegerField(default=0)
    motivo = models.CharField(max_length=200, blank=True, default="")
    before = models.PositiveIntegerField(default=0)
    after = models.PositiveIntegerField(default=0)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_insumos",
    )
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]

    def __str__(self):
        return f"{self.insumo.nombre} - {self.tipo} - {self.cantidad}"
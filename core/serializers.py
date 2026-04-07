from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from rest_framework import serializers

from .models import (
    Paciente,
    ExpedienteClinico,
    SesionClinica,
    Cita,
    NotaClinica,
    RecetaMedica,
    EvidenciaClinica,
    Comentario,
    Servicio,
    Pago,
    BloqueoHorario,
    MovimientoInsumo,
    Insumo,
    StaffProfile,
)


def normalizar_color_hex(valor, fallback="#06b6d4"):
    v = str(valor or "").strip()

    if len(v) == 7 and v.startswith("#"):
        try:
            int(v[1:], 16)
            return v.lower()
        except ValueError:
            return fallback

    if len(v) == 4 and v.startswith("#"):
        try:
            int(v[1:], 16)
            return f"#{v[1]*2}{v[2]*2}{v[3]*2}".lower()
        except ValueError:
            return fallback

    return fallback


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
}


def normalizar_rol_staff(valor, fallback="recepcionista"):
    rol = str(valor or "").strip().lower()
    if not rol:
        return fallback
    return ROLE_ALIASES.get(rol, fallback)


def es_rol_admin(rol):
    return normalizar_rol_staff(rol) in {"doctor", "fisioterapeuta"}


class StaffUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    rol = serializers.CharField(write_only=True, required=False)
    telefono = serializers.CharField(write_only=True, required=False, allow_blank=True)
    descripcion = serializers.CharField(write_only=True, required=False, allow_blank=True)
    foto = serializers.ImageField(write_only=True, required=False, allow_null=True)
    color_agenda = serializers.CharField(write_only=True, required=False, allow_blank=True)

    rol_out = serializers.SerializerMethodField(read_only=True)
    telefono_out = serializers.SerializerMethodField(read_only=True)
    descripcion_out = serializers.SerializerMethodField(read_only=True)
    foto_url = serializers.SerializerMethodField(read_only=True)
    color_agenda_out = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "password",
            "rol",
            "telefono",
            "descripcion",
            "foto",
            "color_agenda",
            "rol_out",
            "telefono_out",
            "descripcion_out",
            "foto_url",
            "color_agenda_out",
        ]

    def get_foto_url(self, obj):
        request = self.context.get("request")
        profile = getattr(obj, "staff_profile", None)
        if not profile or not profile.foto:
            return None
        return request.build_absolute_uri(profile.foto.url) if request else profile.foto.url

    def get_rol_out(self, obj):
        p = getattr(obj, "staff_profile", None)
        return normalizar_rol_staff(p.rol) if p else None

    def get_telefono_out(self, obj):
        p = getattr(obj, "staff_profile", None)
        return p.telefono if p else ""

    def get_descripcion_out(self, obj):
        p = getattr(obj, "staff_profile", None)
        return p.descripcion if p else ""

    def get_color_agenda_out(self, obj):
        p = getattr(obj, "staff_profile", None)
        return normalizar_color_hex(p.color_agenda if p and p.color_agenda else "#06b6d4")

    def validate(self, attrs):
        if self.instance is None:
            if len(attrs.get("password", "")) < 6:
                raise serializers.ValidationError(
                    {"password": "La contraseña debe tener al menos 6 caracteres."}
                )

        if "username" in attrs and not str(attrs.get("username") or "").strip():
            raise serializers.ValidationError({"username": "Usuario requerido."})

        if "email" in attrs:
            attrs["email"] = str(attrs.get("email") or "").strip().lower()

        if "rol" in attrs:
            attrs["rol"] = normalizar_rol_staff(attrs.get("rol"))

        return attrs

    def create(self, validated_data):
        rol = normalizar_rol_staff(validated_data.pop("rol", "recepcionista"))
        telefono = validated_data.pop("telefono", "")
        descripcion = validated_data.pop("descripcion", "")
        foto = validated_data.pop("foto", None)
        color_agenda = normalizar_color_hex(validated_data.pop("color_agenda", "#06b6d4"))
        password = validated_data.pop("password")

        validated_data["email"] = str(validated_data.get("email") or "").strip().lower()

        user = User(**validated_data)
        user.set_password(password)
        user.is_staff = es_rol_admin(rol)
        user.save()

        profile, _ = StaffProfile.objects.get_or_create(
            user=user,
            defaults={
                "rol": rol,
                "telefono": telefono,
                "descripcion": descripcion,
                "foto": foto,
                "color_agenda": color_agenda,
            },
        )

        profile.rol = rol
        profile.telefono = telefono
        profile.descripcion = descripcion
        if foto:
            profile.foto = foto
        profile.color_agenda = color_agenda
        profile.save()

        return user

    def update(self, instance, validated_data):
        rol = validated_data.pop("rol", None)
        if rol is not None:
            rol = normalizar_rol_staff(rol)

        telefono = validated_data.pop("telefono", None)
        descripcion = validated_data.pop("descripcion", None)
        foto = validated_data.pop("foto", None)
        color_agenda = validated_data.pop("color_agenda", None)
        password = validated_data.pop("password", None)

        for key, value in validated_data.items():
            setattr(instance, key, value)

        if password:
            if len(password) < 6:
                raise serializers.ValidationError(
                    {"password": "La contraseña debe tener al menos 6 caracteres."}
                )
            instance.set_password(password)

        if rol is not None:
            instance.is_staff = es_rol_admin(rol)

        instance.save()

        perfil, _ = StaffProfile.objects.get_or_create(
            user=instance,
            defaults={
                "rol": rol or "recepcionista",
                "telefono": telefono or "",
                "descripcion": descripcion or "",
                "color_agenda": normalizar_color_hex(color_agenda or "#06b6d4"),
            },
        )

        if rol is not None:
            perfil.rol = rol
        if telefono is not None:
            perfil.telefono = telefono
        if descripcion is not None:
            perfil.descripcion = descripcion
        if foto is not None:
            perfil.foto = foto
        if color_agenda is not None:
            perfil.color_agenda = normalizar_color_hex(color_agenda)

        perfil.save()
        return instance


class UserSerializer(serializers.ModelSerializer):
    rol = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()
    color_agenda = serializers.SerializerMethodField()
    cedula_profesional = serializers.SerializerMethodField()
    telefono_profesional = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "rol",
            "color_agenda",
            "cedula_profesional",
            "telefono_profesional",
        ]

    def get_full_name(self, obj):
        return (obj.get_full_name() or "").strip() or obj.username

    def get_rol(self, obj):
        sp = getattr(obj, "staff_profile", None)
        if sp and sp.rol:
            return normalizar_rol_staff(sp.rol)
        if obj.is_superuser or obj.is_staff:
            return "doctor"
        return "recepcionista"

    def get_color_agenda(self, obj):
        sp = getattr(obj, "staff_profile", None)
        return sp.color_agenda if sp and sp.color_agenda else "#06b6d4"

    def get_cedula_profesional(self, obj):
        sp = getattr(obj, "staff_profile", None)
        return sp.cedula_profesional if sp and sp.cedula_profesional else ""

    def get_telefono_profesional(self, obj):
        sp = getattr(obj, "staff_profile", None)
        return sp.telefono if sp and sp.telefono else ""


class ExpedienteClinicoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpedienteClinico
        fields = [
            "id",
            "ocupacion",
            "direccion",
            "heredo_familiares",
            "antecedentes",
            "habitos",
            "documentos",
            "notas_generales",
            "creado",
            "actualizado",
        ]
        read_only_fields = ["id", "creado", "actualizado"]


class SesionClinicaSerializer(serializers.ModelSerializer):
    profesional_nombre = serializers.SerializerMethodField(read_only=True)
    cita_id = serializers.SerializerMethodField(read_only=True)
    has_nota_clinica = serializers.SerializerMethodField(read_only=True)
    has_receta_medica = serializers.SerializerMethodField(read_only=True)
    evidencias_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = SesionClinica
        fields = [
            "id",
            "paciente",
            "cita",
            "cita_id",
            "profesional",
            "profesional_nombre",
            "fecha",
            "motivo_consulta",
            "intensidad_dolor",
            "zonas_dolor",
            "notas",
            "exploracion",
            "diagnostico",
            "tratamiento_realizado",
            "recomendaciones",
            "estado_sesion",
            "has_nota_clinica",
            "has_receta_medica",
            "evidencias_count",
            "creado",
            "actualizado",
        ]
        read_only_fields = ["id", "creado", "actualizado"]

    def get_profesional_nombre(self, obj):
        u = obj.profesional
        if not u:
            return ""
        full = f"{u.first_name or ''} {u.last_name or ''}".strip()
        return full or u.username

    def get_cita_id(self, obj):
        return obj.cita_id

    def get_has_nota_clinica(self, obj):
        return bool(obj.cita_id and hasattr(obj.cita, "nota_clinica"))

    def get_has_receta_medica(self, obj):
        return bool(obj.cita_id and hasattr(obj.cita, "receta_medica"))

    def get_evidencias_count(self, obj):
        if not obj.cita_id:
            return 0
        return obj.cita.evidencias_clinicas.count()

    def validate_intensidad_dolor(self, value):
        if value is None:
            return value
        if value < 0 or value > 10:
            raise serializers.ValidationError("La intensidad del dolor debe estar entre 0 y 10.")
        return value

    def validate_zonas_dolor(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("zonas_dolor debe ser una lista.")
        return value


class PacienteSerializer(serializers.ModelSerializer):
    expediente = ExpedienteClinicoSerializer(required=False)
    sesiones_clinicas = SesionClinicaSerializer(many=True, read_only=True)
    nombre_completo = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Paciente
        fields = [
            "id",
            "clinica",
            "nombres",
            "apellido_pat",
            "apellido_mat",
            "nombre_completo",
            "fecha_nac",
            "genero",
            "telefono",
            "correo",
            "molestia",
            "notas",
            "registro",
            "estado_tratamiento",
            "fecha_alta",
            "requiere_factura",
            "facturacion_razon_social",
            "facturacion_rfc",
            "facturacion_regimen_fiscal",
            "facturacion_codigo_postal",
            "facturacion_uso_cfdi",
            "facturacion_correo",
            "expediente",
            "sesiones_clinicas",
        ]
        read_only_fields = ["id", "registro"]

    def get_nombre_completo(self, obj):
        return f"{obj.nombres} {obj.apellido_pat} {obj.apellido_mat or ''}".strip()

    def validate_telefono(self, value):
        return (value or "").strip()

    def validate(self, attrs):
        requiere_factura = attrs.get(
            "requiere_factura",
            getattr(self.instance, "requiere_factura", False),
        )

        if requiere_factura:
            requeridos = {
                "facturacion_razon_social": "Razón social / nombre fiscal",
                "facturacion_rfc": "RFC",
                "facturacion_regimen_fiscal": "Régimen fiscal",
                "facturacion_codigo_postal": "Código postal fiscal",
                "facturacion_uso_cfdi": "Uso CFDI",
                "facturacion_correo": "Correo de facturación",
            }

            errores = {}
            for campo, label in requeridos.items():
                valor = attrs.get(campo, getattr(self.instance, campo, ""))
                if not str(valor or "").strip():
                    errores[campo] = f"{label} es requerido cuando el paciente necesita factura."

            if errores:
                raise serializers.ValidationError(errores)

        return attrs

    def create(self, validated_data):
        expediente_data = validated_data.pop("expediente", None)
        paciente = Paciente.objects.create(**validated_data)

        if expediente_data:
            ExpedienteClinico.objects.create(paciente=paciente, **expediente_data)
        else:
            ExpedienteClinico.objects.create(paciente=paciente)

        return paciente

    def update(self, instance, validated_data):
        expediente_data = validated_data.pop("expediente", None)

        for key, value in validated_data.items():
            setattr(instance, key, value)

        if not instance.requiere_factura:
            instance.facturacion_razon_social = ""
            instance.facturacion_rfc = ""
            instance.facturacion_regimen_fiscal = ""
            instance.facturacion_codigo_postal = ""
            instance.facturacion_uso_cfdi = ""
            instance.facturacion_correo = ""

        instance.save()

        if expediente_data is not None:
            expediente, _ = ExpedienteClinico.objects.get_or_create(paciente=instance)
            for key, value in expediente_data.items():
                setattr(expediente, key, value)
            expediente.save()

        return instance


class PacienteInlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Paciente
        fields = [
            "nombres",
            "apellido_pat",
            "apellido_mat",
            "fecha_nac",
            "genero",
            "telefono",
            "correo",
            "molestia",
            "notas",
        ]


class ComentarioSerializer(serializers.ModelSerializer):
    created_at = serializers.DateTimeField(source="creado", read_only=True)
    objetivo_nombre = serializers.SerializerMethodField(read_only=True)
    objetivo_tag = serializers.SerializerMethodField(read_only=True)
    objetivo_subtitulo = serializers.SerializerMethodField(read_only=True)
    objetivo_foto_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Comentario
        fields = [
            "id",
            "clinica",
            "tipo_objetivo",
            "profesional",
            "servicio",
            "descripcion",
            "calificacion",
            "aprobado",
            "nombre_completo",
            "creado",
            "created_at",
            "objetivo_nombre",
            "objetivo_tag",
            "objetivo_subtitulo",
            "objetivo_foto_url",
        ]
        read_only_fields = [
            "clinica",
            "aprobado",
            "creado",
            "created_at",
            "objetivo_nombre",
            "objetivo_tag",
            "objetivo_subtitulo",
            "objetivo_foto_url",
        ]
        extra_kwargs = {
            "nombre_completo": {"required": False, "allow_blank": True},
            "profesional": {"required": False, "allow_null": True},
            "servicio": {"required": False, "allow_null": True},
        }

    def validate_calificacion(self, value):
        try:
            value = int(value)
        except Exception:
            raise serializers.ValidationError("La calificación debe ser numérica.")

        if value < 1 or value > 5:
            raise serializers.ValidationError("La calificación debe estar entre 1 y 5.")
        return value

    def validate_descripcion(self, value):
        value = (value or "").strip()
        if len(value) < 5:
            raise serializers.ValidationError("El comentario debe tener al menos 5 caracteres.")
        if len(value) > 300:
            raise serializers.ValidationError("El comentario no puede exceder 300 caracteres.")
        return value

    def validate_nombre_completo(self, value):
        return (value or "").strip() or "Paciente anónimo"

    def validate(self, attrs):
        tipo_objetivo = attrs.get("tipo_objetivo") or getattr(self.instance, "tipo_objetivo", None)
        profesional = attrs.get("profesional", getattr(self.instance, "profesional", None))
        servicio = attrs.get("servicio", getattr(self.instance, "servicio", None))

        if tipo_objetivo == "profesional":
            if not profesional:
                raise serializers.ValidationError(
                    {"profesional": "Debes seleccionar el profesional al que pertenece la reseña."}
                )
            if not getattr(profesional, "is_active", False):
                raise serializers.ValidationError(
                    {"profesional": "El profesional seleccionado no está disponible."}
                )
            attrs["servicio"] = None

        elif tipo_objetivo == "servicio":
            if not servicio:
                raise serializers.ValidationError(
                    {"servicio": "Debes seleccionar el servicio al que pertenece la reseña."}
                )
            if not getattr(servicio, "activo", False):
                raise serializers.ValidationError(
                    {"servicio": "El servicio seleccionado no está disponible."}
                )
            attrs["profesional"] = None

        else:
            raise serializers.ValidationError(
                {"tipo_objetivo": "tipo_objetivo debe ser 'profesional' o 'servicio'."}
            )

        attrs["nombre_completo"] = (
            (attrs.get("nombre_completo") or getattr(self.instance, "nombre_completo", "") or "Paciente anónimo")
            .strip()
        )

        return attrs

    def _full_name_user(self, user):
        if not user:
            return ""
        return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username

    def get_objetivo_nombre(self, obj):
        if obj.tipo_objetivo == "profesional" and obj.profesional:
            return self._full_name_user(obj.profesional)
        if obj.tipo_objetivo == "servicio" and obj.servicio:
            return obj.servicio.nombre
        return "Objetivo no disponible"

    def get_objetivo_tag(self, obj):
        return "Servicio" if obj.tipo_objetivo == "servicio" else "Doctor"

    def get_objetivo_subtitulo(self, obj):
        if obj.tipo_objetivo == "profesional" and obj.profesional:
            staff = getattr(obj.profesional, "staff_profile", None)
            if staff and staff.rol:
                return staff.rol
            return "Profesional"

        if obj.tipo_objetivo == "servicio" and obj.servicio:
            return obj.servicio.descripcion or "Servicio"

        return ""

    def get_objetivo_foto_url(self, obj):
        request = self.context.get("request")

        if obj.tipo_objetivo == "profesional" and obj.profesional:
            staff = getattr(obj.profesional, "staff_profile", None)
            if staff and staff.foto:
                return request.build_absolute_uri(staff.foto.url) if request else staff.foto.url

        if obj.tipo_objetivo == "servicio" and obj.servicio and obj.servicio.imagen:
            return request.build_absolute_uri(obj.servicio.imagen.url) if request else obj.servicio.imagen.url

        return None


class ComentarioPublicSerializer(ComentarioSerializer):
    class Meta(ComentarioSerializer.Meta):
        fields = [
            "id",
            "tipo_objetivo",
            "profesional",
            "servicio",
            "nombre_completo",
            "calificacion",
            "descripcion",
            "created_at",
            "objetivo_nombre",
            "objetivo_tag",
            "objetivo_subtitulo",
            "objetivo_foto_url",
        ]
        read_only_fields = fields


class ServicioSerializer(serializers.ModelSerializer):
    imagen_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Servicio
        fields = "__all__"
        extra_kwargs = {
            "clinica": {"read_only": True},
        }

    def get_imagen_url(self, obj):
        request = self.context.get("request")
        if not obj.imagen:
            return None
        url = obj.imagen.url
        return request.build_absolute_uri(url) if request else url


class CitaSerializer(serializers.ModelSerializer):
    paciente_nombre = serializers.SerializerMethodField()
    servicio_nombre = serializers.CharField(source="servicio.nombre", read_only=True)
    profesional_nombre = serializers.SerializerMethodField()

    class Meta:
        model = Cita
        fields = "__all__"
        read_only_fields = ["creado", "actualizado"]

    def get_paciente_nombre(self, obj):
        p = obj.paciente
        return f"{p.nombres} {p.apellido_pat} {p.apellido_mat or ''}".strip()

    def get_profesional_nombre(self, obj):
        u = obj.profesional
        full = f"{u.first_name or ''} {u.last_name or ''}".strip()
        return full or u.username


class CitaCreateSerializer(serializers.ModelSerializer):
    paciente = PacienteInlineSerializer()

    class Meta:
        model = Cita
        fields = [
            "paciente",
            "servicio",
            "profesional",
            "agenda_tipo",
            "fecha",
            "hora_inicio",
            "hora_termina",
            "precio",
            "metodo_pago",
            "estado",
            "notas",
            "pagado",
            "descuento_porcentaje",
            "anticipo",
            "monto_final",
        ]

    def _buscar_paciente_existente(self, clinica, paciente_data):
        nombres = (paciente_data.get("nombres") or "").strip()
        apellido_pat = (paciente_data.get("apellido_pat") or "").strip()
        apellido_mat = (paciente_data.get("apellido_mat") or "").strip()
        fecha_nac = paciente_data.get("fecha_nac")

        if not nombres or not apellido_pat:
            return None

        qs = Paciente.objects.filter(
            clinica=clinica,
            nombres__iexact=nombres,
            apellido_pat__iexact=apellido_pat,
            apellido_mat__iexact=apellido_mat,
        )

        if fecha_nac:
            qs = qs.filter(fecha_nac=fecha_nac)

        return qs.order_by("-id").first()

    def create(self, validated_data):
        paciente_data = validated_data.pop("paciente")
        clinica = self.context["clinica"]

        paciente = self._buscar_paciente_existente(clinica, paciente_data)

        if not paciente:
            paciente = Paciente.objects.create(
                clinica=clinica,
                nombres=paciente_data.get("nombres", ""),
                apellido_pat=paciente_data.get("apellido_pat", ""),
                apellido_mat=paciente_data.get("apellido_mat", ""),
                fecha_nac=paciente_data.get("fecha_nac"),
                genero=paciente_data.get("genero", ""),
                telefono=(paciente_data.get("telefono") or "").strip(),
                correo=(paciente_data.get("correo") or "").strip(),
                molestia=paciente_data.get("molestia", ""),
                notas=paciente_data.get("notas", ""),
            )
            ExpedienteClinico.objects.create(paciente=paciente)

        return Cita.objects.create(paciente=paciente, **validated_data)


class PagoSerializer(serializers.ModelSerializer):
    paciente_nombre = serializers.SerializerMethodField(read_only=True)
    servicio_nombre = serializers.CharField(source="cita.servicio.nombre", read_only=True)
    profesional_id = serializers.IntegerField(source="cita.profesional_id", read_only=True)
    profesional_nombre = serializers.SerializerMethodField(read_only=True)
    fecha_cita = serializers.DateField(source="cita.fecha", read_only=True)
    restante = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = Pago
        fields = [
            "id",
            "cita",
            "fecha_pago",
            "comprobante",
            "monto_facturado",
            "metodo_pago",
            "descuento_porcentaje",
            "anticipo",
            "restante",
            "paciente_nombre",
            "servicio_nombre",
            "profesional_id",
            "profesional_nombre",
            "fecha_cita",
        ]
        extra_kwargs = {
            "comprobante": {"required": False, "allow_blank": True},
            "descuento_porcentaje": {"required": False},
            "anticipo": {"required": False},
        }

    def get_paciente_nombre(self, obj):
        p = obj.cita.paciente
        return f"{p.nombres} {p.apellido_pat} {p.apellido_mat or ''}".strip()

    def get_profesional_nombre(self, obj):
        u = obj.cita.profesional
        full = f"{u.first_name or ''} {u.last_name or ''}".strip()
        return full or u.username

    def validate(self, attrs):
        cita = attrs.get("cita") or getattr(self.instance, "cita", None)
        if not cita:
            return attrs

        if self.instance is not None and "cita" in attrs and attrs["cita"].pk != self.instance.cita_id:
            raise serializers.ValidationError({"cita": "No se puede mover un pago a otra cita."})

        anticipo = Decimal(attrs.get("anticipo") or getattr(self.instance, "anticipo", 0) or 0)
        if anticipo < 0:
            raise serializers.ValidationError({"anticipo": "El anticipo no puede ser negativo."})

        monto_facturado = Decimal(
            attrs.get("monto_facturado", getattr(self.instance, "monto_facturado", cita.precio)) or cita.precio or 0
        )
        descuento_porcentaje = Decimal(
            attrs.get(
                "descuento_porcentaje",
                getattr(self.instance, "descuento_porcentaje", cita.descuento_porcentaje),
            )
            or 0
        )

        desc_amount = (monto_facturado * descuento_porcentaje) / Decimal("100")
        total_con_descuento = max(monto_facturado - desc_amount, Decimal("0"))

        qs_prev = cita.pagos.all()
        if self.instance is not None:
            qs_prev = qs_prev.exclude(pk=self.instance.pk)

        total_pagado_prev = qs_prev.aggregate(total=Sum("anticipo")).get("total") or Decimal("0")
        restante_prev = max(total_con_descuento - total_pagado_prev, Decimal("0"))

        if self.instance is None:
            if restante_prev <= 0 and anticipo > 0:
                raise serializers.ValidationError(
                    {"anticipo": "La cita ya está liquidada; no se puede volver a sumar un pago."}
                )

            if anticipo > restante_prev:
                raise serializers.ValidationError(
                    {"anticipo": f"El pago excede el saldo pendiente. Pendiente actual: ${restante_prev:.2f}"}
                )
        else:
            anticipo_actual = Decimal(getattr(self.instance, "anticipo", 0) or 0)
            maximo_editable = restante_prev + anticipo_actual

            if anticipo > maximo_editable:
                raise serializers.ValidationError(
                    {"anticipo": f"El pago excede el total permitido para esta cita. Máximo: ${maximo_editable:.2f}"}
                )

        return attrs

    def _calcular_saldos(self, *, cita, monto_facturado, descuento_porcentaje, anticipo_nuevo, excluir=None):
        monto = Decimal(monto_facturado or 0)
        desc_pct = Decimal(descuento_porcentaje or 0)
        anticipo_nuevo = Decimal(anticipo_nuevo or 0)

        desc_amount = (monto * desc_pct) / Decimal("100")
        total_con_descuento = max(monto - desc_amount, Decimal("0"))

        qs_prev = cita.pagos.all()
        if excluir is not None:
            qs_prev = qs_prev.exclude(pk=excluir.pk)

        total_pagado_prev = qs_prev.aggregate(total=Sum("anticipo")).get("total") or Decimal("0")
        total_pagado_actual = total_pagado_prev + anticipo_nuevo
        restante = max(total_con_descuento - total_pagado_actual, Decimal("0"))

        return restante, total_con_descuento, total_pagado_actual

    def _actualizar_campos_cita(
        self,
        *,
        cita,
        descuento_porcentaje,
        total_con_descuento,
        total_pagado_actual,
        restante,
        metodo_pago,
    ):
        cita.descuento_porcentaje = descuento_porcentaje
        cita.monto_final = total_con_descuento
        cita.anticipo = total_pagado_actual
        cita.pagado = restante <= 0

        if metodo_pago:
            cita.metodo_pago = metodo_pago

        cita.save(
            update_fields=[
                "descuento_porcentaje",
                "monto_final",
                "anticipo",
                "pagado",
                "metodo_pago",
                "actualizado",
            ]
        )

    def create(self, validated_data):
        cita = validated_data["cita"]
        monto_facturado = validated_data.get("monto_facturado") or cita.precio
        descuento_porcentaje = validated_data.get("descuento_porcentaje", cita.descuento_porcentaje)
        anticipo = validated_data.get("anticipo") or 0
        metodo_pago = validated_data.get("metodo_pago") or ""

        restante, total_con_descuento, total_pagado_actual = self._calcular_saldos(
            cita=cita,
            monto_facturado=monto_facturado,
            descuento_porcentaje=descuento_porcentaje,
            anticipo_nuevo=anticipo,
        )

        pago = Pago.objects.create(**validated_data, restante=restante)

        self._actualizar_campos_cita(
            cita=cita,
            descuento_porcentaje=descuento_porcentaje,
            total_con_descuento=total_con_descuento,
            total_pagado_actual=total_pagado_actual,
            restante=restante,
            metodo_pago=metodo_pago,
        )

        return pago

    def update(self, instance, validated_data):
        cita = instance.cita
        monto_facturado = validated_data.get("monto_facturado", instance.monto_facturado or cita.precio)
        descuento_porcentaje = validated_data.get(
            "descuento_porcentaje", instance.descuento_porcentaje or cita.descuento_porcentaje
        )
        anticipo = validated_data.get("anticipo", instance.anticipo or 0)
        metodo_pago = validated_data.get("metodo_pago", instance.metodo_pago or "")

        restante, total_con_descuento, total_pagado_actual = self._calcular_saldos(
            cita=cita,
            monto_facturado=monto_facturado,
            descuento_porcentaje=descuento_porcentaje,
            anticipo_nuevo=anticipo,
            excluir=instance,
        )

        for key, value in validated_data.items():
            setattr(instance, key, value)

        instance.restante = restante
        instance.save()

        self._actualizar_campos_cita(
            cita=cita,
            descuento_porcentaje=descuento_porcentaje,
            total_con_descuento=total_con_descuento,
            total_pagado_actual=total_pagado_actual,
            restante=restante,
            metodo_pago=metodo_pago,
        )

        return instance


class BloqueoHorarioSerializer(serializers.ModelSerializer):
    profesional_nombre = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BloqueoHorario
        fields = "__all__"

    def get_profesional_nombre(self, obj):
        u = obj.profesional
        if not u:
            return ""
        full = f"{u.first_name or ''} {u.last_name or ''}".strip()
        return full or u.username


class MovimientoInsumoSerializer(serializers.ModelSerializer):
    creado_por_nombre = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = MovimientoInsumo
        fields = "__all__"

    def get_creado_por_nombre(self, obj):
        u = getattr(obj, "creado_por", None)
        if not u:
            return ""
        return f"{u.first_name or ''} {u.last_name or ''}".strip() or u.username


class InsumoSerializer(serializers.ModelSerializer):
    movimientos = MovimientoInsumoSerializer(many=True, read_only=True)
    clinica = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Insumo
        fields = "__all__"
        extra_kwargs = {
            "clinica": {"read_only": True},
        }


class NotaClinicaSerializer(serializers.ModelSerializer):
    paciente_nombre = serializers.SerializerMethodField(read_only=True)
    profesional_nombre = serializers.SerializerMethodField(read_only=True)
    profesional_cedula = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = NotaClinica
        fields = "__all__"

    def get_paciente_nombre(self, obj):
        p = getattr(obj, "paciente", None)
        if not p:
            return ""
        return f"{p.nombres} {p.apellido_pat} {p.apellido_mat or ''}".strip()

    def get_profesional_nombre(self, obj):
        u = getattr(obj, "profesional", None)
        if not u:
            return ""
        return f"{u.first_name or ''} {u.last_name or ''}".strip() or u.username

    def get_profesional_cedula(self, obj):
        u = getattr(obj, "profesional", None)
        perfil = getattr(u, "staff_profile", None) if u else None
        return getattr(perfil, "cedula_profesional", "") or ""


class RecetaMedicaSerializer(serializers.ModelSerializer):
    paciente_nombre = serializers.SerializerMethodField(read_only=True)
    profesional_nombre = serializers.SerializerMethodField(read_only=True)
    profesional_cedula = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = RecetaMedica
        fields = "__all__"

    def get_paciente_nombre(self, obj):
        p = getattr(obj, "paciente", None)
        if not p:
            return ""
        return f"{p.nombres} {p.apellido_pat} {p.apellido_mat or ''}".strip()

    def get_profesional_nombre(self, obj):
        u = getattr(obj, "profesional", None)
        if not u:
            return ""
        return f"{u.first_name or ''} {u.last_name or ''}".strip() or u.username

    def get_profesional_cedula(self, obj):
        u = getattr(obj, "profesional", None)
        perfil = getattr(u, "staff_profile", None) if u else None
        return getattr(perfil, "cedula_profesional", "") or ""


class EvidenciaClinicaSerializer(serializers.ModelSerializer):
    archivo_url = serializers.SerializerMethodField(read_only=True)
    archivo_nombre = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = EvidenciaClinica
        fields = "__all__"

    def get_archivo_url(self, obj):
        if not getattr(obj, "archivo", None):
            return None

        request = self.context.get("request")
        url = obj.archivo.url
        return request.build_absolute_uri(url) if request else url

    def get_archivo_nombre(self, obj):
        if not getattr(obj, "archivo", None):
            return ""
        return (obj.archivo.name or "").split("/")[-1]

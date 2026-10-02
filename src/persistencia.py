"""Persistencia del historial térmico con SQLAlchemy e idempotencia (HU-22).

Criterios de aceptación implementados:
  - Escenario 1: Persiste telemetría válida completa en PostgreSQL con columnas tipadas, aun sin IA.
  - Escenario 2: Preserva clasificación de IA (clase, probabilidad, versión de modelo, timestamp) sin alterar telemetría original.
  - Escenario 3: Si la base de datos falla, rollback y excepción (impide enviar acuse committed al nodo).
  - Escenario 4: Idempotencia estricta por UNIQUE(device_id, boot_id, seq_no). Ante colisión con diferente contenido, se audita el conflicto.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker, Session

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "contrato"))
from telemetria_v1 import TelemetriaV1

Base = declarative_base()
logger = logging.getLogger("coldcare.persistencia")


class LecturaTermica(Base):
    __tablename__ = "lecturas_termicas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(50), nullable=False, index=True)
    boot_id = Column(BigInteger, nullable=False)
    seq_no = Column(BigInteger, nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False)
    time_quality = Column(String(20), nullable=False)
    uptime_s = Column(BigInteger, nullable=False)

    temp_ds18b20 = Column(Float, nullable=True)
    ds18b20_status = Column(String(20), nullable=False)
    ds18b20_fault = Column(String(30), nullable=True)

    temp_sht31 = Column(Float, nullable=True)
    humedad_sht31 = Column(Float, nullable=True)
    sht31_status = Column(String(20), nullable=False)
    sht31_fault = Column(String(30), nullable=True)

    door_open = Column(Boolean, nullable=True)
    door_open_s = Column(Integer, nullable=True)
    door_open_streak_s = Column(Integer, nullable=True)
    mc38_status = Column(String(20), nullable=False)

    power_source = Column(String(20), nullable=True)
    sensor_fault = Column(Boolean, nullable=False)
    firmware_version = Column(String(20), nullable=False)
    simulated = Column(Boolean, default=False)

    # Clasificación IA complementaria (Escenario 2)
    ia_clase = Column(String(30), nullable=True)  # 'normal' | 'riesgo_preventivo'
    ia_probabilidad = Column(Float, nullable=True)
    ia_model_version = Column(String(20), nullable=True)
    ia_inferido_at = Column(DateTime(timezone=True), nullable=True)

    payload_raw = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("device_id", "boot_id", "seq_no", name="uq_lectura_identidad"),
    )


class ConflictoLectura(Base):
    __tablename__ = "conflictos_lectura"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(50), nullable=False)
    boot_id = Column(BigInteger, nullable=False)
    seq_no = Column(BigInteger, nullable=False)
    detectado_at = Column(DateTime(timezone=True), nullable=False)
    payload_previo = Column(Text, nullable=False)
    payload_nuevo = Column(Text, nullable=False)


class RepositorioLecturas:
    def __init__(self, db_url: str = "sqlite:///:memory:"):
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def persistir_lectura(
        self,
        t: TelemetriaV1,
        raw_payload: str,
        clasificacion_ia: dict[str, Any] | None = None,
        received_at: datetime | None = None,
    ) -> tuple[str, LecturaTermica | None]:
        """Persiste la telemetría en la base de datos con manejo estricto de idempotencia.

        Retorna ('committed', entidad) o ('duplicate', entidad_existente).
        Lanza excepción si la base de datos no está disponible.
        """
        session: Session = self.SessionLocal()
        ahora = received_at or datetime.now(timezone.utc)

        lectura = LecturaTermica(
            device_id=t.device_id,
            boot_id=t.boot_id,
            seq_no=t.seq_no,
            captured_at=t.captured_at,
            received_at=ahora,
            time_quality=t.time_quality,
            uptime_s=t.uptime_s,
            temp_ds18b20=t.temp_ds18b20,
            ds18b20_status=t.ds18b20_status,
            ds18b20_fault=t.ds18b20_fault,
            temp_sht31=t.temp_sht31,
            humedad_sht31=t.humedad_sht31,
            sht31_status=t.sht31_status,
            sht31_fault=t.sht31_fault,
            door_open=t.door_open,
            door_open_s=t.door_open_s,
            door_open_streak_s=t.door_open_streak_s,
            mc38_status=t.mc38_status,
            power_source=t.power_source,
            sensor_fault=t.sensor_fault,
            firmware_version=t.firmware_version,
            simulated=bool(t.simulated),
            payload_raw=raw_payload,
        )

        if clasificacion_ia:
            lectura.ia_clase = clasificacion_ia.get("clase")
            lectura.ia_probabilidad = clasificacion_ia.get("probabilidad")
            lectura.ia_model_version = clasificacion_ia.get("model_version")
            lectura.ia_inferido_at = clasificacion_ia.get("inferido_at") or ahora

        try:
            session.add(lectura)
            session.commit()
            session.refresh(lectura)
            return "committed", lectura
        except IntegrityError:
            session.rollback()
            # Escenario 4: Conflicto de unicidad
            stmt = select(LecturaTermica).where(
                LecturaTermica.device_id == t.device_id,
                LecturaTermica.boot_id == t.boot_id,
                LecturaTermica.seq_no == t.seq_no,
            )
            existente = session.scalars(stmt).first()
            if existente:
                try:
                    payload_previo_json = json.loads(existente.payload_raw)
                    payload_nuevo_json = json.loads(raw_payload)
                    if payload_previo_json != payload_nuevo_json:
                        conflicto = ConflictoLectura(
                            device_id=t.device_id,
                            boot_id=t.boot_id,
                            seq_no=t.seq_no,
                            detectado_at=ahora,
                            payload_previo=existente.payload_raw,
                            payload_nuevo=raw_payload,
                        )
                        session.add(conflicto)
                        session.commit()
                except Exception as ex:
                    logger.error("Error al registrar conflicto: %s", ex)
                    session.rollback()
            return "duplicate", existente
        except Exception as e:
            session.rollback()
            logger.critical("Falla de persistencia en base de datos: %s", e)
            raise
        finally:
            session.close()

    def contar_lecturas(self) -> int:
        with self.SessionLocal() as session:
            return session.query(LecturaTermica).count()

    def contar_conflictos(self) -> int:
        with self.SessionLocal() as session:
            return session.query(ConflictoLectura).count()

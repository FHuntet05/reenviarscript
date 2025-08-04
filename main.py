# main.py
import asyncio
import os
import json
import time
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict

from telethon.sync import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Message

# --- CONFIGURACIÓN DE LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- CLASE DE CONFIGURACIÓN (REDISEÑADA PARA ENRUTAMIENTO) ---
@dataclass
class Config:
    api_id: int
    api_hash: str
    session_string: str
    
    # Mapeo de categorías a IDs de canal de destino
    destination_channels: Dict[str, int]
    
    # Mapeo de IDs de canal de origen a su categoría principal
    source_mapping: Dict[int, str]
    
    sleep_interval_seconds: int = 5 * 60 * 60

    @classmethod
    def from_env(cls):
        """Carga toda la configuración desde las variables de entorno."""
        try:
            api_id = int(os.environ['API_ID'])
            api_hash = os.environ['API_HASH']
            session_string = os.environ['SESSION_STRING']
        except KeyError as e:
            raise ValueError(f"❌ Variable de entorno básica faltante: {e}")
        except ValueError:
            raise ValueError("❌ API_ID debe ser un número entero.")

        # 1. Cargar destinos
        dest_channels = {}
        # Categorías que esperamos encontrar.
        CATEGORIES = ['MOVIES', 'SERIES', 'ANIME', 'DORAMAS', 'RETRO_TV', 'MIXED_UNSORTED']
        for category in CATEGORIES:
            env_var = f'{category}_DEST_ID'
            dest_id_str = os.environ.get(env_var)
            if dest_id_str:
                try:
                    dest_channels[category.upper()] = int(dest_id_str)
                except ValueError:
                    raise ValueError(f"El ID para {env_var} no es un número entero válido.")
        
        if not dest_channels:
            raise ValueError("❌ No se ha configurado ningún canal de destino (ej. MOVIES_DEST_ID).")
        if 'MIXED_UNSORTED' not in dest_channels:
            raise ValueError("❌ Es obligatorio configurar 'MIXED_UNSORTED_DEST_ID' como canal de fallback.")

        # 2. Cargar y mapear orígenes
        source_map = {}
        # Lista de posibles categorías de origen
        SOURCE_CATEGORIES = ['MOVIES', 'SERIES', 'ANIME', 'DORAMAS', 'RETRO_TV', 'MIXED']
        for category in SOURCE_CATEGORIES:
            env_var = f'{category}_SOURCE_IDS'
            source_ids_str = os.environ.get(env_var)
            if source_ids_str:
                try:
                    source_ids = [int(sid.strip()) for sid in source_ids_str.split(',')]
                    for sid in source_ids:
                        source_map[sid] = category.upper() # Mapea el ID de origen a su categoría
                except ValueError:
                    raise ValueError(f"Los IDs en {env_var} deben ser números enteros separados por comas.")

        if not source_map:
            raise ValueError("❌ No se ha configurado ningún canal de origen (ej. MOVIES_SOURCE_IDS o MIXED_SOURCE_IDS).")

        sleep_seconds = int(os.environ.get('SLEEP_INTERVAL_SECONDS', 5 * 60 * 60))

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            session_string=session_string,
            destination_channels=dest_channels,
            source_mapping=source_map,
            sleep_interval_seconds=sleep_seconds
        )

# --- PARSER INTELIGENTE (MODIFICADO PARA DETECTAR MÁS CATEGORÍAS) ---
class CaptionParser:
    PATTERNS = {
        'SERIES': [
            r'\bS\d{1,2}E\d{1,3}\b', r'\b\d{1,2}x\d{1,3}\b', r'temporada\s*\d{1,2}', 
            r'\bT\d{1,2}\b', r'capitulo\s*\d{1,3}', r'episodio\s*\d{1,3}', 
            r'\bEp\s*\d{1,3}\b', r'\bCap\s*\d{1,3}\b'
        ],
        'ANIME': [
            r'sub\s*español', r'subtitulado'
        ],
        'DORAMAS': [
            r'\b(k-drama|c-drama|j-drama|dorama)\b'
        ]
    }

    @staticmethod
    def _clean_text(text: str) -> str:
        text = text.encode('ascii', 'ignore').decode('ascii')
        text = re.sub(r'http\S+|www.\S+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    @staticmethod
    def classify_and_parse(caption: str) -> Tuple[str, str, Optional[str], Optional[str]]:
        if not caption:
            return "UNCLASSIFIED", "Video sin título", None, None

        text_lower = caption.lower()
        detected_category = "MOVIES"

        for category, patterns in CaptionParser.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    detected_category = category
                    break
            if detected_category != "MOVIES":
                break
        
        clean_caption = CaptionParser._clean_text(caption)
        quality_match = re.search(r'\[?\b(4k|2160p|1080p|720p|480p|HD|HQ|WEB-DL|WEBRip|BluRay|BRRip|HDRip)\b\]?', clean_caption, re.IGNORECASE)
        quality = quality_match.group(1).upper() if quality_match else None
        if quality_match: clean_caption = clean_caption.replace(quality_match.group(0), '')

        year_match = re.search(r'\b(19[89]\d|20\d{2})\b', clean_caption)
        year = year_match.group(0) if year_match else None
        if year_match: clean_caption = clean_caption.replace(year_match.group(0), '')
        
        title = clean_caption.strip('()[]{}-_. ').replace('  ', ' ')
        title = title.split('\n')[0].strip()
        if not title: title = "Título no detectado"

        return detected_category, title, quality, year

# --- GESTOR DE ESTADO (sin cambios) ---
class StateManager:
    def __init__(self, file_path: str = 'processed_state.json'):
        self.file_path = file_path
        self.state = self._load()
    def _load(self):
        try:
            with open(self.file_path, 'r') as f:
                data = json.load(f)
                data.setdefault('last_processed_ids', {})
                data.setdefault('processed_signatures', [])
                data['last_processed_ids'] = {int(k): v for k, v in data['last_processed_ids'].items()}
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            logging.info("No se encontró un archivo de estado. Creando uno nuevo.")
            return {'last_processed_ids': {}, 'processed_signatures': []}
    def _save(self):
        with open(self.file_path, 'w') as f: json.dump(self.state, f, indent=4)
    def get_last_message_id(self, chat_id: int) -> int: return self.state['last_processed_ids'].get(chat_id, 0)
    def update_last_message_id(self, chat_id: int, message_id: int):
        self.state['last_processed_ids'][chat_id] = message_id
        self._save()
    def has_signature(self, signature: str) -> bool: return signature in self.state['processed_signatures']
    def add_signature(self, signature: str):
        self.state['processed_signatures'].append(signature)
        self._save()

# --- CLASE FORWARDER (LÓGICA DE ENRUTAMIENTO PRINCIPAL) ---
class Forwarder:
    def __init__(self, config: Config, state: StateManager):
        self.config = config
        self.state = state
        self.client = TelegramClient(StringSession(config.session_string), config.api_id, config.api_hash)
        self.dest_entities = {}

    @staticmethod
    def _is_video(message: Message) -> bool:
        if message.video: return True
        if message.document and message.document.size > 20 * 1024 * 1024:
            if any(k in message.document.mime_type for k in ['video', 'x-matroska']):
                return True
        return False

    @staticmethod
    def _create_signature(message: Message, parsed_title: str) -> str:
        file_size = message.document.size if message.document else message.video.size
        duration_attr = next((attr for attr in getattr(message.video or message.document, 'attributes', []) if hasattr(attr, 'duration')), None)
        duration = duration_attr.duration if duration_attr else 0
        normalized_title = re.sub(r'\W+', '', parsed_title).lower()
        return f"{normalized_title}-{file_size}-{duration}"

    async def _process_channel(self, source_entity: Channel):
        source_id = source_entity.id
        source_category = self.config.source_mapping.get(source_id, "MIXED")
        last_message_id = self.state.get_last_message_id(source_id)
        logging.info(f"🎞️  Escaneando '{source_entity.title}' (Cat: {source_category}) desde ID: {last_message_id}...")
        
        videos_processed = 0
        
        async for message in self.client.iter_messages(source_entity, min_id=last_message_id):
            if not self._is_video(message):
                self.state.update_last_message_id(source_id, message.id)
                continue

            original_caption = message.text or ""
            detected_category, parsed_title, quality, year = CaptionParser.classify_and_parse(original_caption)
            
            signature = self._create_signature(message, parsed_title)
            if self.state.has_signature(signature):
                logging.info(f"  -> ⏭️  Duplicado por firma: '{parsed_title}'.")
                self.state.update_last_message_id(source_id, message.id)
                continue
            
            final_category = "UNCLASSIFIED"
            if source_category != "MIXED":
                final_category = source_category
            else:
                final_category = detected_category
            
            if final_category == "UNCLASSIFIED":
                final_category = "MIXED_UNSORTED"

            new_caption_parts = [parsed_title]
            if quality: new_caption_parts.append(f"[{quality}]")
            if year: new_caption_parts.append(f"[{year}]")
            new_caption = " ".join(new_caption_parts)

            dest_entity = self.dest_entities.get(final_category.upper())
            if not dest_entity:
                logging.warning(f"  -> ⚠️ No se encontró un destino para la categoría '{final_category}'. Saltando.")
                self.state.update_last_message_id(source_id, message.id)
                continue
            
            logging.info(f"  -> 📥 '{parsed_title}' -> Cat: [{final_category}] -> Enviando a '{getattr(dest_entity, 'title', 'N/A')}'")
            
            try:
                await self.client.send_file(dest_entity, file=message, caption=new_caption)
                self.state.add_signature(signature)
                videos_processed += 1
            except FloodWaitError as e:
                logging.warning(f"⏳ Flood wait. Durmiendo por {e.seconds + 5} seg.")
                await asyncio.sleep(e.seconds + 5)
            except Exception as e:
                logging.error(f"⚠️ Error enviando video ID {message.id}: {e}")
                await asyncio.sleep(5)
            
            self.state.update_last_message_id(source_id, message.id)

        if videos_processed > 0:
            logging.info(f"✅ Escaneo de '{source_entity.title}' completado. {videos_processed} videos nuevos procesados.")
        return videos_processed

    async def run(self):
        await self.client.start()
        me = await self.client.get_me()
        logging.info(f"🚀 Cliente conectado como: {me.first_name}")

        logging.info("📡 Verificando y cargando entidades de destino...")
        for category, dest_id in self.config.destination_channels.items():
            try:
                entity = await self.client.get_entity(dest_id)
                self.dest_entities[category.upper()] = entity
                logging.info(f"  -> ✅ Destino '{category}': '{getattr(entity, 'title', dest_id)}'")
            except Exception as e:
                logging.error(f"❌ FATAL: No se pudo encontrar el canal de destino para {category} ({dest_id}). Error: {e}")
                return

        source_entities = []
        logging.info("📡 Verificando canales de origen...")
        source_ids_to_check = list(self.config.source_mapping.keys())
        for source_id in source_ids_to_check:
            try:
                entity = await self.client.get_entity(source_id)
                source_entities.append(entity)
                logging.info(f"  -> ✅ Origen '{getattr(entity, 'title', source_id)}' (Cat: {self.config.source_mapping[source_id]})")
            except Exception as e:
                logging.warning(f"  -> ⚠️  ADVERTENCIA: No se pudo encontrar el origen {source_id}. Se omitirá. Error: {e}")
        
        if not source_entities:
            logging.error("❌ No se encontró ningún canal de origen válido. Saliendo.")
            return

        while True:
            logging.info("="*50)
            logging.info(f"🔄 Iniciando nuevo ciclo de escaneo: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            total_processed = 0
            for source_entity in source_entities:
                total_processed += await self._process_channel(source_entity)
            logging.info(f"✅ Ciclo completado. Total de videos nuevos: {total_processed}")
            logging.info(f"😴 Durmiendo por {self.config.sleep_interval_seconds / 3600:.1f} horas...")
            await asyncio.sleep(self.config.sleep_interval_seconds)

async def main():
    try:
        config = Config.from_env()
        state = StateManager()
        forwarder = Forwarder(config, state)
        await forwarder.run()
    except (ValueError, Exception) as e:
        logging.error(f"🔥 Error fatal al iniciar el bot: {e}")

if __name__ == "__main__":
    asyncio.run(main())

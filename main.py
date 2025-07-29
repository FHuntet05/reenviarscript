# main.py - Versión con Medidas de Seguridad Anti-Baneo Avanzadas
import asyncio
import os
import re
import time
import pickle
import random
from datetime import datetime, timezone
from telethon.sync import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.sessions import StringSession

# --- CONFIGURACIÓN DE SEGURIDAD (A TRAVÉS DE VARIABLES DE ENTORNO) ---
API_ID = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
SESSION_STRING = os.environ.get('SESSION_STRING')
SOURCE_CHAT_IDS_STR = os.environ.get('SOURCE_CHAT_IDS')
DESTINATION_CHAT_ID_STR = os.environ.get('DESTINATION_CHAT_ID')

# Filtros
MIN_VIDEO_SIZE_MB = 20

# 1. Ritmo "Humano" (Pausas aleatorias entre cada video)
MIN_DELAY_SECONDS = int(os.environ.get('MIN_DELAY_SECONDS', 5))
MAX_DELAY_SECONDS = int(os.environ.get('MAX_DELAY_SECONDS', 15))

# 2. Descansos por Lotes
BATCH_SIZE = int(os.environ.get('BATCH_SIZE', 75))
MIN_BATCH_BREAK_MINUTES = int(os.environ.get('MIN_BATCH_BREAK_MINUTES', 15))
MAX_BATCH_BREAK_MINUTES = int(os.environ.get('MAX_BATCH_BREAK_MINUTES', 30))

# 3. "Modo Noche" (El bot duerme)
SLEEP_START_HOUR_UTC = int(os.environ.get('SLEEP_START_HOUR_UTC', 23)) # 11 PM UTC
SLEEP_END_HOUR_UTC = int(os.environ.get('SLEEP_END_HOUR_UTC', 7))     # 7 AM UTC

# --- VALIDACIÓN Y GESTIÓN DE ESTADO (Sin cambios) ---
# ... (Se mantiene el código de validación, load/save processed_videos, clean_caption, y las funciones de interfaz)
if not all([API_ID, API_HASH, SESSION_STRING, SOURCE_CHAT_IDS_STR, DESTINATION_CHAT_ID_STR]):
    raise ValueError("Faltan una o más variables de entorno requeridas.")
try:
    SOURCE_CHAT_IDS = [int(chat_id.strip()) for chat_id in SOURCE_CHAT_IDS_STR.split(',')]
    DESTINATION_CHAT_ID = int(DESTINATION_CHAT_ID_STR)
    API_ID = int(API_ID)
except ValueError:
    raise ValueError("Los IDs de canal y el API_ID deben ser números enteros.")

PROCESSED_VIDEOS_FILE = 'processed_videos.dat'

def load_processed_videos():
    try:
        with open(PROCESSED_VIDEOS_FILE, 'rb') as f:
            return pickle.load(f)
    except (FileNotFoundError, EOFError):
        return set()

def save_processed_videos(processed_set):
    with open(PROCESSED_VIDEOS_FILE, 'wb') as f:
        pickle.dump(processed_set, f)

def clean_caption(text):
    if not text: return ""
    text = re.sub(r'https?://\S+|www\.\S+|t\.me/\S+', '', text)
    text = re.sub(r'@\w+', '', text)
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE)
    text = emoji_pattern.sub(r'', text)
    text = re.sub(r'\s{2,}', ' ', text); text = re.sub(r'(\n\s*){2,}', '\n\n', text)
    return text.strip()

def print_progress_detailed(current, total, start_time, channel_name):
    if total == 0: return
    progress_percentage = current / total; bar_length = 25
    filled_length = int(bar_length * progress_percentage); bar = '█' * filled_length + '─' * (bar_length - filled_length)
    elapsed_time = time.time() - start_time; vpm = (current / elapsed_time) * 60 if elapsed_time > 0 else 0
    time_per_item = elapsed_time / current if current > 0 else 0; remaining_time = time_per_item * (total - current)
    elapsed_str = time.strftime('%H:%M:%S', time.gmtime(elapsed_time)); eta_str = time.strftime('%H:%M:%S', time.gmtime(remaining_time))
    print(f"\r[ {channel_name[:20]:<20} ] {bar} {current}/{total} ({progress_percentage:.1%}) | Vel: {vpm:.1f} v/min | ETA: {eta_str} | Transcurrido: {elapsed_str}", end='', flush=True)
    if current == total: print()

async def send_chapter_header(client, dest_entity, channel_name, channel_id):
    header_text = (f"╭─── • ◆ • ───╮\n  COMENZANDO RECOPILACIÓN\n╰─── • ◆ • ───╯\n\n"
                   f"📁 **Origen:** `{channel_name}`\n🆔 **ID:** `{channel_id}`")
    await client.send_message(dest_entity, header_text, parse_mode='md')

async def send_chapter_footer(client, dest_entity, channel_name, count):
    footer_text = (f"╭─── • ◆ • ───╮\n  RECOPILACIÓN FINALIZADA\n╰─── • ◆ • ───╯\n\n"
                   f"✅ Se transfirieron **{count}** videos desde `{channel_name}`.")
    await client.send_message(dest_entity, footer_text, parse_mode='md')


# --- NUEVA FUNCIÓN DE SEGURIDAD: MODO NOCHE ---
def is_sleep_time():
    """Verifica si la hora actual UTC está dentro del rango de sueño."""
    now_utc = datetime.now(timezone.utc)
    start = SLEEP_START_HOUR_UTC
    end = SLEEP_END_HOUR_UTC

    # Maneja el caso en que el rango cruza la medianoche (ej: 23:00 a 07:00)
    if start > end:
        return now_utc.hour >= start or now_utc.hour < end
    else:
        return start <= now_utc.hour < end

# --- FUNCIÓN PRINCIPAL MODIFICADA ---
async def main():
    print("🚀 Iniciando cliente de Telegram en MODO SEGURO...")
    async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
        # ... (código de conexión y verificación de entidades sin cambios)
        me = await client.get_me(); print(f"✅ Cliente conectado como: {me.first_name}\n")
        try:
            dest_entity = await client.get_entity(DESTINATION_CHAT_ID)
            print(f"🎯 Canal de destino: {getattr(dest_entity, 'title', dest_entity.id)}\n")
        except Exception as e:
            print(f"❌ Error fatal al encontrar el canal de destino: {e}"); return
        source_entities = []
        print("📡 Verificando canales de origen...")
        for source_id in SOURCE_CHAT_IDS:
            try:
                entity = await client.get_entity(source_id); source_entities.append(entity)
                print(f"  -> ✅ Encontrado: {getattr(entity, 'title', entity.id)}")
            except Exception as e:
                print(f"  -> ⚠️  Advertencia al buscar {source_id}: {e}")
        if not source_entities: print("❌ Error fatal: No se encontró ningún canal de origen válido."); return
        
        # El ciclo principal ahora se ejecuta indefinidamente, pero con pausas
        while True:
            # --- VERIFICACIÓN DE MODO NOCHE ---
            if is_sleep_time():
                print(f"🌙 Modo Noche activado. El bot está durmiendo hasta las {SLEEP_END_HOUR_UTC}:00 UTC. Próxima verificación en 15 minutos...")
                await asyncio.sleep(900) # Espera 15 minutos antes de volver a chequear
                continue # Salta el resto del ciclo y vuelve a chequear la hora

            print("\n" + "="*60); print(f"🔄 Iniciando nuevo ciclo de escaneo: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            processed_videos = load_processed_videos()
            
            for source_entity in source_entities:
                # ... (lógica de escaneo y filtrado sin cambios)
                channel_name = getattr(source_entity, 'title', f"ID:{source_entity.id}")
                print(f"\n🔎 Escaneando canal: '{channel_name}'")
                videos_to_process = []
                try:
                    async for msg in client.iter_messages(source_entity):
                        if (msg.video and msg.id not in processed_videos and msg.video.size > MIN_VIDEO_SIZE_MB * 1024 * 1024):
                            videos_to_process.append(msg)
                except Exception as e:
                    print(f"  -> ❌ Error al escanear: {e}"); continue
                if not videos_to_process:
                    print(f"  -> No se encontraron videos nuevos o mayores a {MIN_VIDEO_SIZE_MB}MB."); continue
                
                videos_to_process.sort(key=lambda m: m.text if m.text else '')
                total_to_process = len(videos_to_process)
                print(f"  -> {total_to_process} videos nuevos para procesar. Iniciando...")
                await send_chapter_header(client, dest_entity, channel_name, source_entity.id)
                start_time = time.time()
                
                for i, message in enumerate(videos_to_process, start=1):
                    # --- REVISIÓN DE MODO NOCHE DENTRO DEL BUCLE ---
                    if is_sleep_time():
                        print("\n🌙 Se activó el Modo Noche durante un lote. Pausando hasta la mañana...")
                        break # Rompe el bucle del lote actual para entrar en modo sueño

                    try:
                        print_progress_detailed(i, total_to_process, start_time, channel_name)
                        original_caption = message.text; cleaned_caption = clean_caption(original_caption)
                        
                        await client.send_file(dest_entity, message.media, caption=cleaned_caption, supports_streaming=True)
                        
                        processed_videos.add(message.id)
                        
                        # --- PAUSA ALEATORIA "HUMANA" ---
                        human_like_delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
                        await asyncio.sleep(human_like_delay)

                        # --- DESCANSO DE LOTE ---
                        if i % BATCH_SIZE == 0 and i < total_to_process:
                            batch_break_minutes = random.uniform(MIN_BATCH_BREAK_MINUTES, MAX_BATCH_BREAK_MINUTES)
                            print(f"\n☕ Fin del lote. Tomando un descanso de {batch_break_minutes:.1f} minutos...")
                            await asyncio.sleep(batch_break_minutes * 60)
                            print("👍 Descanso finalizado. Reanudando...")

                    except FloodWaitError as e:
                        # --- MANEJO DE ERRORES CAUTELOSO ---
                        extra_wait = random.uniform(10, 30)
                        wait_time = e.seconds + extra_wait
                        print(f"\n⏳ FloodWait. Telegram pide esperar {e.seconds}s. Esperaremos {wait_time:.0f}s por seguridad...")
                        await asyncio.sleep(wait_time)
                    except Exception as e:
                        print(f"\n⚠️ Error procesando video ID {message.id}: {e}")
                        await asyncio.sleep(15) # Pausa más larga en caso de error desconocido

                if not is_sleep_time(): # Solo enviar footer si no nos detuvimos por modo noche
                    await send_chapter_footer(client, dest_entity, channel_name, total_to_process)
                    print(f"\n✅ Procesamiento de '{channel_name}' completado.")
            
            print("\n" + "="*60); print("✅ ¡Ciclo completado!"); save_processed_videos(processed_videos)
            print(f"💾 Estado guardado. {len(processed_videos)} videos en total procesados. Esperando próximo ciclo activo...")
            await asyncio.sleep(60) # Espera 1 minuto antes de iniciar el siguiente gran ciclo (o chequear si es hora de dormir)

if __name__ == "__main__":
    asyncio.run(main())

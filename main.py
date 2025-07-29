# main.py (Versión RÁPIDA con capítulos, filtro de tamaño y progreso detallado)
import asyncio
import os
import time
import pickle
from telethon.sync import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.sessions import StringSession

# --- CONFIGURACIÓN MEDIANTE VARIABLES DE ENTORNO ---
API_ID = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
SESSION_STRING = os.environ.get('SESSION_STRING')
SOURCE_CHAT_IDS_STR = os.environ.get('SOURCE_CHAT_IDS')
DESTINATION_CHAT_ID_STR = os.environ.get('DESTINATION_CHAT_ID')
SLEEP_INTERVAL_SECONDS = int(os.environ.get('SLEEP_INTERVAL_SECONDS', 3600))
MIN_VIDEO_SIZE_MB = 20  # Filtro: Mínimo de MB para reenviar un video

# --- VALIDACIÓN DE LA CONFIGURACIÓN ---
if not all([API_ID, API_HASH, SESSION_STRING, SOURCE_CHAT_IDS_STR, DESTINATION_CHAT_ID_STR]):
    raise ValueError("❌ Faltan una o más variables de entorno")
try:
    SOURCE_CHAT_IDS = [int(chat_id.strip()) for chat_id in SOURCE_CHAT_IDS_STR.split(',')]
    DESTINATION_CHAT_ID = int(DESTINATION_CHAT_ID_STR)
    API_ID = int(API_ID)
except ValueError:
    raise ValueError("❌ IDs de canales o API_ID no son números enteros.")


# --- LÓGICA PARA EVITAR DUPLICADOS ---
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

# --- BARRA DE PROGRESO ULTRA-DETALLADA ---
def print_progress_detailed(current, total, start_time, channel_name):
    if total == 0: return
    
    progress_percentage = current / total
    bar_length = 25
    filled_length = int(bar_length * progress_percentage)
    bar = '█' * filled_length + '─' * (bar_length - filled_length)

    elapsed_time = time.time() - start_time
    vpm = (current / elapsed_time) * 60 if elapsed_time > 0 else 0
    time_per_item = elapsed_time / current if current > 0 else 0
    remaining_time = time_per_item * (total - current)
    elapsed_str = time.strftime('%H:%M:%S', time.gmtime(elapsed_time))
    eta_str = time.strftime('%H:%M:%S', time.gmtime(remaining_time))

    print(
        f"\r[ {channel_name[:20]:<20} ] {bar} {current}/{total} ({progress_percentage:.1%}) | "
        f"Vel: {vpm:.1f} v/min | "
        f"ETA: {eta_str} | "
        f"Transcurrido: {elapsed_str}",
        end=''
        flush=True
    )
    if current == total:
        print()

async def send_chapter_header(client, dest_entity, channel_name, channel_id):
    header_text = (
        f"╭─── • ◆ • ───╮\n"
        f"  COMENZANDO RECOPILACIÓN\n"
        f"╰─── • ◆ • ───╯\n\n"
        f"📁 **Origen:** `{channel_name}`\n"
        f"🆔 **ID:** `{channel_id}`"
    )
    await client.send_message(dest_entity, header_text, parse_mode='md')

async def send_chapter_footer(client, dest_entity, channel_name, count):
    footer_text = (
        f"╭─── • ◆ • ───╮\n"
        f"  RECOPILACIÓN FINALIZADA\n"
        f"╰─── • ◆ • ───╯\n\n"
        f"✅ Se transfirieron **{count}** videos desde `{channel_name}`."
    )
    await client.send_message(dest_entity, footer_text, parse_mode='md')

async def main():
    print("🚀 Iniciando cliente de Telegram...")
    
    async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"✅ Cliente conectado como: {me.first_name}\n")

        try:
            dest_entity = await client.get_entity(DESTINATION_CHAT_ID)
            print(f"🎯 Canal de destino: {getattr(dest_entity, 'title', dest_entity.id)}\n")
        except Exception as e:
            print(f"❌ Error fatal al encontrar el canal de destino: {e}")
            return

        source_entities = []
        print("📡 Verificando canales de origen...")
        for source_id in SOURCE_CHAT_IDS:
            try:
                entity = await client.get_entity(source_id)
                source_entities.append(entity)
                print(f"  -> ✅ Encontrado: {getattr(entity, 'title', entity.id)}")
            except Exception as e:
                print(f"  -> ⚠️  Advertencia al buscar {source_id}: {e}")
        
        if not source_entities:
            print("❌ Error fatal: No se encontró ningún canal de origen válido.")
            return

        while True:
            print("\n" + "="*60)
            print(f"🔄 Iniciando nuevo ciclo de escaneo: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            processed_videos = load_processed_videos()
            
            for source_entity in source_entities:
                channel_name = getattr(source_entity, 'title', f"ID:{source_entity.id}")
                print(f"\n🔎 Escaneando canal: '{channel_name}'")
                
                videos_to_forward = []
                try:
                    async for msg in client.iter_messages(source_entity):
                        if (msg.video and 
                            msg.id not in processed_videos and 
                            msg.video.size > MIN_VIDEO_SIZE_MB * 1024 * 1024):
                            videos_to_forward.append(msg)
                except Exception as e:
                    print(f"  -> ❌ Error al escanear: {e}")
                    continue

                if not videos_to_forward:
                    print(f"  -> No se encontraron videos nuevos o mayores a {MIN_VIDEO_SIZE_MB}MB.")
                    continue

                # Ordenar alfabéticamente por el texto del mensaje
                videos_to_forward.sort(key=lambda m: m.text if m.text else '')
                
                total_to_forward = len(videos_to_forward)
                print(f"  -> {total_to_forward} videos nuevos para reenviar. Iniciando...")

                # 1. ENVIAR CABECERA
                await send_chapter_header(client, dest_entity, channel_name, source_entity.id)
                
                start_time = time.time()
                
                # 2. BUCLE DE REENVÍO DIRECTO
                for i, message in enumerate(videos_to_forward, start=1):
                    try:
                        print_progress_detailed(i, total_to_forward, start_time, channel_name)
                        
                        # Usar forward_messages para máxima velocidad
                        await client.forward_messages(dest_entity, message)
                        
                        processed_videos.add(message.id)
                        await asyncio.sleep(2) # Pausa recomendada para no saturar la API
                    
                    except FloodWaitError as e:
                        print(f"\n⏳ FloodWait: Esperando {e.seconds}s...")
                        await asyncio.sleep(e.seconds)
                    except Exception as e:
                        print(f"\n⚠️ Error reenviando video ID {message.id}: {e}")
                        await asyncio.sleep(5)

                # 3. ENVIAR PIE DE PÁGINA
                await send_chapter_footer(client, dest_entity, channel_name, total_to_forward)
                print(f"\n✅ Reenvío de '{channel_name}' completado.")
            
            print("\n" + "="*60)
            print("✅ ¡Ciclo completado!")
            save_processed_videos(processed_videos)
            print(f"💾 Estado guardado. {len(processed_videos)} videos en total procesados.")
            print(f"😴 Durmiendo por {SLEEP_INTERVAL_SECONDS / 60:.0f} minutos...")
            await asyncio.sleep(SLEEP_INTERVAL_SECONDS)

if __name__ == "__main__":
    asyncio.run(main())

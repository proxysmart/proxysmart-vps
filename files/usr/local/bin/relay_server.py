#!/usr/bin/env python3

import asyncio
import struct
import uuid
import re,sys
import websockets
import ssl


from datetime import datetime, timedelta, date

# ----- глобальные структуры -----
# client_name -> (reader, writer)
backconnect_clients = {}
backconnect_lock = asyncio.Lock()

# channel_id -> внешний клиент (writer, и возможно задача на чтение)
channels = {}
channels_lock = asyncio.Lock()

domain_global=''
auth_global=''

# ----- вспомогательные функции -----
def compute_channel_id() -> int:
    return uuid.uuid4().int & 0xFFFFFFFF

async def recv_message(websocket ) -> tuple[int, bytes] | None:
    """Читает (channel_id, data) из сокета - для протокола между VPS и backconnect-клиентом."""
    try:
        buf = await websocket.recv()
        cid_data = buf[0:4]
        cid = struct.unpack('!I', cid_data)[0]
        data = buf[4:]
        print("<= PC", cid, data[:100] )
        return cid, data
    except (asyncio.IncompleteReadError, ConnectionResetError):
        return None

async def send_message(websocket, cid: int, data: bytes):
    """Отправляет сообщение backconnect-клиенту."""
    print('=> PC', cid, data[:100])
    msg = struct.pack('!I', cid) + data
    try:
        await websocket.send (msg)
    except:
        pass

# ----- обработка backconnect-клиента (ПК) -----
async def handle_backconnect_client ( websocket ):
    global domain_global
    global auth_global
    addr = websocket.remote_address 
    w_id=  websocket.id
    print(f"[+] Backconnect connected from {addr}")

    try:
        # регистрация
        client_name=''
        reg_line = await websocket.recv()
        if not reg_line:
            return
        parts = reg_line.decode().strip().split()
        #print(  parts)
        if len(parts) != 3 or parts[0] != "REGISTER":
            print(f"[!] Wrong initial handshake from {addr}")
            await websocket.send (b"ERROR\n")
            return
        auth = parts[1]
        client_name = parts[2]

        if domain_global!='-' and not client_name.endswith(domain_global):
            print(f"[!] cant accept this client '{client_name}', the domain must be within {domain_global}")
            await websocket.send (b"ERROR\n")
            return

        #print('auth', auth)
        if auth_global!='-' and auth!=auth_global:
            print(f"[!] cant accept this client '{client_name}', auth not passed")
            await websocket.send (b"ERROR\n")
            return
            
        async with backconnect_lock:
            bc_info = backconnect_clients.get(client_name)
            if bc_info:
                print(f"[!] Another client '{client_name}' tried to register while there is a running client with the same name {addr}")
                await websocket.send (b"ERROR\n")
                return
            else:
                #finally register
                backconnect_clients[client_name] = [ websocket , datetime.now() ] 

        await websocket.send(b"OK\n")
        print(f"[+] Client '{client_name}' registered from {addr}")

        # цикл приёма сообщений (данные от ПК, которые идут к внешним клиентам)
        while True:
            msg = await recv_message ( websocket )
            if msg is None:
                break
            #print(  msg)
            cid, data = msg
            #data=data[:250]+b"\r\n\r\n"
            async with channels_lock:
                client_info = channels.get(cid)
            #print( client_info )
            if client_info is None:
                print(f"[-] Received data for unknown channel {cid}, dropping")
                continue
            ext_writer, _ = client_info
            #print(  ext_writer )
            if data==b'':
                ext_writer.write(b'HTTP/1.1 502 Backend WebApp not available\r\n\r\nWebApp Error\r\n')
                print(f"[!] PC told me to close this channel")
                await close_channel(cid, send_close_to_pc=False)
            try:
                ext_writer.write(data)
                #print('=>ext', data )
                await ext_writer.drain()
            except (ConnectionError, BrokenPipeError):
                # внешний клиент закрыт, чистим канал
                await close_channel(cid, send_close_to_pc=False)

    except Exception as e:
        print(f"[!] Error in backconnect handler {client_name}: {e}")
    finally:
        is_registered = False

        if client_name:
            async with backconnect_lock:
                #pop() only when current writer matches dict's writer
                bc_info = backconnect_clients.get( client_name )
                if bc_info and bc_info[0].id  == w_id :
                    is_registered=True
                    #print( 'pop', is_registered ,  addr)
                    backconnect_clients.pop(client_name, None)
                
        await websocket.close() 
        print(f"[-] Backconnect client (registered:{is_registered}) '{client_name}' disconnected {addr}")
        # все каналы этого клиента закрываются
        if is_registered:
            cids_to_close=[]
            async with channels_lock:
                for cid, client_info in channels.items() :
                    if  client_info[0] == client_name:
                        cids_to_close.append( cid )
            for cid in cids_to_close:
                await close_channel(cid, send_close_to_pc=False)

# ----- закрытие канала -----
async def close_channel(cid: int, send_close_to_pc: bool = True):
    print(f"[!] Closing channel {cid}")
    async with channels_lock:
        info = channels.pop(cid, None)
    if info is None:
        print(f"[!] channel {cid} already closed ")
        return
    try:
        ext_writer.close()
        await ext_writer.wait_closed()
    except:
        pass
    # оповещаем backconnect-клиента о закрытии канала (пустое сообщение)
    if send_close_to_pc:
        #print(33333)
        client_name = meta.get('client_name')
        if client_name:
            async with backconnect_lock:
                bc_info = backconnect_clients.get(client_name)
            if bc_info :
                websocket, _ = bc
                print(f"[!] notified PC to close channel {cid}")
                await send_message( websocket, cid, b'')  # нулевая длина сигнализирует закрытие

# ----- парсинг Host из первых данных -----
def extract_host_from_first_data(data: bytes) -> str | None:
    """
    Извлекает fsqdn из строки Host: ... 
    """
    # Ищем Host: в первых 4096 байтах (достаточно для GET запроса)
    try:
        text = data.decode('ascii', errors='ignore')
    except:
        return None
    match = re.search(rf'Host:\s*([a-zA-Z0-9\-\.]+)\b', text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

# ----- обработка внешнего клиента (браузер/curl) -----
async def handle_external_tcp_client(reader, writer, ):
    global domain_global
    addr = writer.get_extra_info('peername')
    print(f"[*] New external connection from {addr}")

    # Прочитаем первые данные, чтобы определить Host
    first_chunk = await reader.read(4096)
    if not first_chunk:
        writer.close()
        return

    #print(  first_chunk )
    fqdn = extract_host_from_first_data(first_chunk)

    client_name=''
#    if domain_global=='-':
#        client_name=fqdn
#    else:
#        m=re.search(rf'^(.+)\.{domain_global}', fqdn)
#        if m:
#            client_name=m.group(1)

    client_name=fqdn

    if not client_name:
        writer.write(b'HTTP/1.1 400 Bad Request\r\n\r\nError\r\n')
        print(f"[!] Missing valid Host header from {addr}")
        await writer.drain()
        writer.close()
        return

    # Находим backconnect-клиента
    async with backconnect_lock:
        bc_info = backconnect_clients.get(client_name)
    if not bc_info:
        writer.write(b'HTTP/1.1 400 Bad Request\r\nContent-type: text/html\r\n\r\n<h2>WebApp not available</h2>Error\r\n')
        print(f"[!] Missing client {client_name} from {addr}")
        await writer.drain()
        writer.close()
        return

    websocket, _  = bc_info
    # Генерируем ID канала
    cid = compute_channel_id()
    print(f"[*] Generated channel {cid} from {addr}")

    # Сохраняем канал
    async with channels_lock:
        channels[cid] = ( writer , client_name) 

    # Отправляем backconnect-клиенту первый кусок данных (уже прочитанный)
    await send_message( websocket, cid, first_chunk)

    # Теперь запускаем две задачи: пересылка от внешнего клиента к ПК
    async def forward_from_external():
        try:
            while True:
                data = await reader.read(65536)
                #print('fwd', data)
                if not data:
                    #print('break')
                    break
                await send_message( websocket, cid, data)
        except Exception:
            pass
        finally:
            await close_channel(cid, send_close_to_pc=True)

    # задача пересылки от ПК к внешнему клиенту уже обрабатывается в основном цикле handle_backconnect
    # (там мы пишем в ext_writer, найденный по cid)
    # Но нам нужно также знать, что канал закрылся – об этом позаботится close_channel.

    try:
        # Запускаем forward_from_external в фоне
        fwd_task = asyncio.create_task(forward_from_external())
        # Ждём, пока канал не закроется (close_channel вызовется либо при ошибке, либо при закрытии внешнего клиента)
        await fwd_task
    except:
        pass
    finally:
        await close_channel(cid, send_close_to_pc=True)

# ----- запуск серверов -----
async def start_backconnect_server( bk_scheme='wss',  host='0.0.0.0', port=4446,  ):

    if bk_scheme=='wss':
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain ( '/etc/ssl/certs/ssl-cert-snakeoil.pem', '/etc/ssl/private/ssl-cert-snakeoil.key', )
    else:
        ssl_context=None

    print(f"[*] WS Backconnect server listening on {bk_scheme}://{host}:{port}")
    async with websockets.serve( 
        handle_backconnect_client , 
        host, 
        port,
        ssl=ssl_context ,
        ):
        await asyncio.Future()

async def start_external_tcp_proxy(host='127.0.0.1', port=3082,  ):
    server = await asyncio.start_server(handle_external_tcp_client, host, port,  )
    global domain_global
    if domain_global=='-':  domain_desc='any domain name'
    else: domain_desc=f'subdomains of {domain_global}'
    print(f"[*] External TCP proxy listening on {host}:{port} (HTTP/WebSocket supported), { domain_desc }")
    async with server:
        await server.serve_forever()

async def start_logging():
    while True:
        await asyncio.sleep(5)
        async with backconnect_lock:
            print('[?] BackConnect clients: ', ", ".join(list ( backconnect_clients.keys() ) ))
        async with channels_lock:
            print('[?] Active channels: ',  list ( channels.keys() ) )

async def main( bk_scheme='ws', port_backconnect_server=4446, port_external_tcp_proxy=3082, domain='', auth='', ):
    global domain_global
    global auth_global
    if domain:
        domain_global=domain 
    if auth:
        auth_global=auth
    await asyncio.gather(
        start_backconnect_server( bk_scheme, '0.0.0.0',   port_backconnect_server,   ),
        start_external_tcp_proxy( '127.0.0.1', port_external_tcp_proxy, ),
        start_logging(),
    )

def usage():
    print (f"""
    Usage: relay_server.py <scheme> <port_backconnect_server> <port_external_tcp_proxy> <domain> <auth>
        scheme      : ws or wss
        port_backconnect_server: 2096  (for backconnect clients)
        port_external_tcp_proxy:    3082  (for Web browsers)
        domain :    smth like back.com or - (hyphen) for any domain
        auth:   login:password  or - for no auth
""")


if __name__ == '__main__':
    if len(sys.argv) ==6:
        asyncio.run(main( *sys.argv[1:]     ))
    else:
        usage()

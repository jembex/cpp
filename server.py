import socket
import threading
import struct
import json
import time

# Store connected clients: {id: {'socket': sock, 'addr': addr}}
clients = {}
clients_lock = threading.Lock()
next_client_id = 1

def send_all(sock, data):
    try:
        total = 0
        length = len(data)
        while total < length:
            sent = sock.send(data[total:])
            if sent == 0: return False
            total += sent
        return True
    except:
        return False

def recv_all(sock, length):
    try:
        data = b''
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk: return None
            data += chunk
        return data
    except:
        return None

def bridge_connection(admin_sock, client_sock):
    """Bridges traffic between admin and client until one disconnects"""
    def forward(source, dest, name):
        try:
            while True:
                data = source.recv(4096)
                if not data: break
                dest.sendall(data)
        except:
            pass
        # If loop breaks, connection is dead.
        # We don't explicitly close here, we let the outer scope handle cleanup or let natural TCP FIN propagate.
        # Actually, if one side closes, we should close the other to signal end.
        try: dest.close() 
        except: pass
        try: source.close()
        except: pass

    print("Starting bridge...")
    t1 = threading.Thread(target=forward, args=(admin_sock, client_sock, "A->C"), daemon=True)
    t2 = threading.Thread(target=forward, args=(client_sock, admin_sock, "C->A"), daemon=True)
    t1.start()
    t2.start()
    
    t1.join()
    t2.join()
    print("Bridge ended.")

def handle_admin(sock):
    """Handle Admin Connection"""
    try:
        while True:
            # Check for commands.
            # Admin sends raw bytes, but let's say commands are short strings?
            # Or we peek?
            # In our Admin code:
            # LIST -> send_all(sock, b"LIST")
            # CONN -> send_all(sock, f"CONN {id}".encode())
            
            # Simple text protocol for control
            data = sock.recv(1024)
            if not data: break
            
            cmd_str = data.decode('utf-8').strip()
            
            if cmd_str == "LIST":
                with clients_lock:
                    client_list = [{'id': k, 'ip': v['addr'][0]} for k, v in clients.items()]
                
                json_bytes = json.dumps(client_list).encode('utf-8')
                send_all(sock, struct.pack('i', len(json_bytes)))
                send_all(sock, json_bytes)
                break # Close after list (Admin design choice)
                
            elif cmd_str.startswith("CONN "):
                try:
                    headers = cmd_str.split()
                    target_id = int(headers[1])
                    
                    target_sock = None
                    with clients_lock:
                        if target_id in clients:
                            # Pop client to give exclusive access to this Admin?
                            # Yes, because socket can only bridge to one.
                            client_obj = clients.pop(target_id)
                            target_sock = client_obj['socket']
                    
                    if target_sock:
                        send_all(sock, b"OK")
                        # Enter Bridge Mode
                        bridge_connection(sock, target_sock)
                        # When bridge returns, connections are closed.
                        return
                    else:
                        send_all(sock, b"NO")
                        break
                except Exception as e:
                    print(f"Conn error: {e}")
                    break
            else:
                break
    except Exception as e:
        print(f"Admin Error: {e}")
    finally:
        sock.close()

def handle_unknown(sock, addr):
    """Determine if Admin or Client"""
    global next_client_id
    
    # We give the connector 2 seconds to say "ADMIN"
    # The current bot is silent upon connection.
    try:
        sock.settimeout(2.0)
        first_bytes = sock.recv(5)
        sock.settimeout(None)
        
        if first_bytes == b"ADMIN":
            print(f"Admin connected from {addr}")
            handle_admin(sock)
        else:
            # It's a Bot (or it sent garbage, we assume Bot)
            # If it sent something that wasn't ADMIN, we might have eaten part of its protocol?
            # But Bot protocol is passive (waits for server). So Bot sends NOTHING.
            # If recv times out => Bot.
            # If recv returns data != ADMIN => Unknown, but treat as Bot?
            # Actually, if Bot is silent, recv throws Timeout.
            pass
            
            # This path is unreachable if Timeout occurred.
            # We catch timeout below.
            
    except socket.timeout:
        # Timeout means silent connection => Bot
        sock.settimeout(None)
        with clients_lock:
            cid = next_client_id
            next_client_id += 1
            clients[cid] = {'socket': sock, 'addr': addr}
        
        print(f"[+] Client {cid} connected from {addr}")
        
        # We store it and do nothing. The socket waits in memory.
        # We need a way to detect if it dies while waiting.
        # Peek?
        # For now, we trust TCP keepalives or future errors.
        return

    except Exception as e:
        print(f"Handshake error: {e}")
        sock.close()

def accept_thread(server_socket):
    while True:
        try:
            client_sock, addr = server_socket.accept()
            threading.Thread(target=handle_unknown, args=(client_sock, addr), daemon=True).start()
        except Exception as e:
            print(f"Accept error: {e}")

def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server_sock.bind(('0.0.0.0', 5000))
        server_sock.listen(5)
        print("Relay Server Listening on Port 5000...")
        
        accept_thread(server_sock)
    except Exception as e:
        print(f"Startup failed: {e}")

if __name__ == "__main__":
    main()

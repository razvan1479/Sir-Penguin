<?php
/**
 * ============================================================
 * api.php — API tickete Metin2 <-> Discord (EXEMPLU COMPLET)
 * ============================================================
 * Il pui pe site (ex. https://site-ul-tau.ro/tickets/api.php) si
 * completezi setarile de mai jos. Merge pe MySQL SI MariaDB identic.
 *
 * In dashboardul botului (pagina "Metin2 tickete") pui:
 *   - Adresa API:  https://site-ul-tau.ro/tickets/api.php
 *   - Token:       acelasi ca $API_TOKEN de mai jos
 *
 * Rutele (exact ca in METIN2_TICKET_API.md):
 *   GET  api.php/tickets/pending
 *   POST api.php/tickets/{id}/link
 *   GET  api.php/messages/pending
 *   POST api.php/messages/ack
 *   POST api.php/tickets/{id}/message
 *   POST api.php/tickets/{id}/status
 *   GET  api.php/categories
 */

/* ============ SETARI — COMPLETEAZA AICI ============ */
$DB_HOST   = '127.0.0.1';
$DB_NAME   = 'numele_bazei_site';   // baza de date a site-ului
$DB_USER   = 'user_mysql';
$DB_PASS   = 'parola_mysql';
$API_TOKEN = 'SCHIMBA-MA-cu-un-sir-lung-random';  // acelasi il pui in dashboardul botului
/* =================================================== */

header('Content-Type: application/json; charset=utf-8');

/* ---------- autentificare: Authorization: Bearer <token> ---------- */
$auth = $_SERVER['HTTP_AUTHORIZATION'] ?? ($_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '');
if (!hash_equals('Bearer ' . $API_TOKEN, $auth)) {
    http_response_code(401);
    echo json_encode(['error' => 'unauthorized']);
    exit;
}

/* ---------- conexiune DB (PDO — identic MySQL/MariaDB) ---------- */
try {
    $pdo = new PDO(
        "mysql:host=$DB_HOST;dbname=$DB_NAME;charset=utf8mb4",
        $DB_USER, $DB_PASS,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
         PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
    );
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode(['error' => 'db_connection_failed']);
    exit;
}

/* ---------- routing (robust: merge cu PATH_INFO sau fara) ---------- */
$path = $_SERVER['PATH_INFO'] ?? null;
if ($path === null || $path === '') {
    // scoatem numele scriptului din URL DOAR daca e chiar un .php
    // (sub php -S, SCRIPT_NAME e calea cererii, nu fisierul - nu-l taiem)
    $uri    = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
    $script = $_SERVER['SCRIPT_NAME'] ?? '';
    if ($script !== '' && stripos($script, '.php') !== false && strpos($uri, $script) === 0) {
        $path = substr($uri, strlen($script));
    } else {
        $path = $uri;
    }
    if ($path === '' || $path === false) { $path = '/'; }
}
$method = $_SERVER['REQUEST_METHOD'];
$body   = json_decode(file_get_contents('php://input'), true) ?: [];

/* ============ 1. GET /tickets/pending ============
   Ticketele create in joc, inca nepreluate de bot. */
if ($method === 'GET' && $path === '/tickets/pending') {
    $rows = $pdo->query(
        "SELECT id, player_name, player_id, title, description,
                category, status, created_at
         FROM ticket
         WHERE discord_channel_id IS NULL
         ORDER BY id ASC LIMIT 20"
    )->fetchAll();
    echo json_encode(['tickets' => $rows]);
    exit;
}

/* ============ 2. POST /tickets/{id}/link ============
   Botul ne spune ce canal Discord a creat pentru ticket. */
if ($method === 'POST' && preg_match('#^/tickets/(\d+)/link$#', $path, $m)) {
    $st = $pdo->prepare("UPDATE ticket SET discord_channel_id = ? WHERE id = ?");
    $st->execute([(string)($body['discord_channel_id'] ?? ''), (int)$m[1]]);
    echo json_encode(['ok' => true]);
    exit;
}

/* ============ 3. GET /messages/pending ============
   Mesaje noi de la JUCATORI, nepreluate inca de bot. */
if ($method === 'GET' && $path === '/messages/pending') {
    $rows = $pdo->query(
        "SELECT id, ticket_id, sender, sender_name, text, image_url, created_at
         FROM ticket_message
         WHERE sender = 'player' AND sent_to_discord = 0
         ORDER BY id ASC LIMIT 50"
    )->fetchAll();
    // ticket_id ca numar (json_encode il poate face string altfel)
    foreach ($rows as &$r) { $r['ticket_id'] = (int)$r['ticket_id']; $r['id'] = (int)$r['id']; }
    echo json_encode(['messages' => $rows]);
    exit;
}

/* ============ 4. POST /messages/ack ============
   Botul confirma ce mesaje a pus pe Discord. */
if ($method === 'POST' && $path === '/messages/ack') {
    $ids = array_filter(array_map('intval', $body['message_ids'] ?? []));
    if ($ids) {
        $in = implode(',', array_fill(0, count($ids), '?'));
        $st = $pdo->prepare("UPDATE ticket_message SET sent_to_discord = 1 WHERE id IN ($in)");
        $st->execute($ids);
    }
    echo json_encode(['ok' => true]);
    exit;
}

/* ============ 5. POST /tickets/{id}/message ============
   Raspuns de la STAFF (din Discord) -> il salvam sa apara in joc.
   sent_to_discord=1 direct (vine DE PE Discord, nu-l mai trimitem inapoi). */
if ($method === 'POST' && preg_match('#^/tickets/(\d+)/message$#', $path, $m)) {
    $st = $pdo->prepare(
        "INSERT INTO ticket_message (ticket_id, sender, sender_name, text, image_url, sent_to_discord)
         VALUES (?, 'staff', ?, ?, ?, 1)"
    );
    $st->execute([
        (int)$m[1],
        mb_substr((string)($body['sender_name'] ?? 'Staff'), 0, 100),
        (string)($body['text'] ?? ''),
        $body['image_url'] ?? null,
    ]);
    echo json_encode(['ok' => true]);
    exit;
}

/* ============ 6. POST /tickets/{id}/status ============ */
if ($method === 'POST' && preg_match('#^/tickets/(\d+)/status$#', $path, $m)) {
    $status = (string)($body['status'] ?? '');
    if (!in_array($status, ['open', 'in_progress', 'resolved'], true)) {
        http_response_code(400);
        echo json_encode(['error' => 'bad_status']);
        exit;
    }
    $st = $pdo->prepare("UPDATE ticket SET status = ? WHERE id = ?");
    $st->execute([$status, (int)$m[1]]);
    echo json_encode(['ok' => true]);
    exit;
}

/* ============ 7. GET /categories ============ */
if ($method === 'GET' && $path === '/categories') {
    $rows = $pdo->query("SELECT name FROM ticket_category ORDER BY id")->fetchAll(PDO::FETCH_COLUMN);
    echo json_encode(['categories' => $rows]);
    exit;
}

/* ---------- ruta necunoscuta ---------- */
http_response_code(404);
echo json_encode(['error' => 'not_found']);

-- ============================================================
-- Schema tickete Metin2 <-> Discord
-- Merge IDENTIC pe MySQL si MariaDB (nu trebuie ales nimic).
-- Rulezi o singura data in baza de date a site-ului
-- (phpMyAdmin -> SQL, sau linia de comanda).
-- ============================================================

CREATE TABLE IF NOT EXISTS ticket_category (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- cateva categorii de start (le poti schimba)
INSERT IGNORE INTO ticket_category (name) VALUES
  ('Account Issues'), ('Bug Reports'), ('Payment'), ('Other');

CREATE TABLE IF NOT EXISTS ticket (
  id INT AUTO_INCREMENT PRIMARY KEY,
  player_name VARCHAR(100) NOT NULL,
  player_id VARCHAR(64) NOT NULL,
  title VARCHAR(200) NOT NULL,
  description TEXT,
  category VARCHAR(100) DEFAULT NULL,
  status ENUM('open','in_progress','resolved') NOT NULL DEFAULT 'open',
  discord_channel_id VARCHAR(32) DEFAULT NULL,  -- gol pana il preia botul
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_pending (discord_channel_id),
  INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ticket_message (
  id INT AUTO_INCREMENT PRIMARY KEY,
  ticket_id INT NOT NULL,
  sender ENUM('player','staff') NOT NULL,
  sender_name VARCHAR(100) NOT NULL,
  text TEXT,
  image_url VARCHAR(500) DEFAULT NULL,
  sent_to_discord TINYINT(1) NOT NULL DEFAULT 0,  -- 1 = botul l-a preluat deja
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_ticket (ticket_id),
  INDEX idx_pending (sent_to_discord, sender),
  CONSTRAINT fk_tm_ticket FOREIGN KEY (ticket_id)
    REFERENCES ticket(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- Cum insereaza SITE-UL TAU un ticket nou (cand jucatorul il
-- deschide din interfata) — exemplu:
--
-- INSERT INTO ticket (player_name, player_id, title, description, category)
-- VALUES ('Razvan', 'acc_4571', 'Nu pot intra pe cont', 'Parola gresita...', 'Account Issues');
--
-- Si un mesaj nou de la jucator:
--
-- INSERT INTO ticket_message (ticket_id, sender, sender_name, text)
-- VALUES (123, 'player', 'Razvan', 'Am incercat si de pe alt PC.');
--
-- Restul (preluarea pe Discord, raspunsurile staff) le face botul prin api.php.
-- ============================================================

-- ============================================================
-- 瑜伽约课系统 PostgreSQL → MySQL 迁移脚本
-- 目标库: yuekeDB (MySQL 8.0+)
-- 范围: 11 张表 + 8 个有源码定义的数据库函数
-- 注意: 15 个未公开函数体的 fn_* 需后续根据后端接口补齐
-- ============================================================

SET NAMES utf8mb4;

-- ------------------------------------------------------------
-- 1. 建表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `user` (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    open_id       TEXT,
    name          TEXT,
    nick_name     TEXT,
    avatar_url    TEXT,
    phone         TEXT,
    gender        INT,
    address       TEXT,
    note          TEXT,
    user_type     INT DEFAULT 0,
    creation_time BIGINT DEFAULT 0,
    updated_time  BIGINT DEFAULT 0,
    INDEX idx_user_open_id (open_id(64)),
    INDEX idx_user_type (user_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS lesson (
    id   INT AUTO_INCREMENT PRIMARY KEY,
    name TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS coach (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    name         TEXT,
    thumbnail    TEXT,
    introduction TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS course (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    lesson_id     INT,
    teacher_id    INT,
    date_time     BIGINT COMMENT '当天0点时间戳(秒)',
    start_time    INT COMMENT '开课时间(秒)',
    end_time      INT,
    peoples       INT COMMENT '容量',
    class_type    INT COMMENT '位掩码:1小班/2私教/4团课',
    hidden        INT DEFAULT 0 COMMENT '1=停课',
    creation_time BIGINT DEFAULT 0,
    updated_time  BIGINT DEFAULT 0,
    INDEX idx_course_date (date_time),
    INDEX idx_course_type (class_type),
    INDEX idx_course_teacher (teacher_id),
    INDEX idx_course_lesson (lesson_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS reservation (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    course_id     INT,
    user_id       INT,
    fulfill       INT DEFAULT 0 COMMENT '1=有效约课',
    vc_id         INT DEFAULT 0 COMMENT '所用会员卡id',
    creation_time BIGINT DEFAULT 0,
    updated_time  BIGINT DEFAULT 0,
    INDEX idx_res_course (course_id),
    INDEX idx_res_user (user_id),
    INDEX idx_res_vc (vc_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS vip_card (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT,
    card_id    INT COMMENT '1周卡/2月卡/3年卡/4次卡',
    start_date BIGINT DEFAULT 0,
    end_date   BIGINT DEFAULT 0,
    times      INT,
    hidden     INT DEFAULT 0 COMMENT '0有效/1过期/2次数用完',
    INDEX idx_vc_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slideshow (
    id    INT AUTO_INCREMENT PRIMARY KEY,
    image TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `function` (
    id    INT AUTO_INCREMENT PRIMARY KEY,
    name  TEXT,
    image TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS announcement (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    title        TEXT,
    content      LONGTEXT,
    updated_time BIGINT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    title        TEXT,
    slogan       TEXT,
    content      LONGTEXT,
    updated_time BIGINT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS systeminfo (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    open_id       TEXT,
    sdk_version   TEXT,
    brand         TEXT,
    model         TEXT,
    pixel_ratio   TEXT,
    platform      TEXT,
    screen_height TEXT,
    screen_width  TEXT,
    version       TEXT,
    ip            TEXT,
    `count`       INT DEFAULT 0,
    creation_time BIGINT DEFAULT 0,
    updated_time  BIGINT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 2. 函数: fn_user_query —— 按 openid 查用户资料 (返回 JSON)
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS fn_user_query;
DELIMITER $$
CREATE FUNCTION fn_user_query(in_id TEXT)
RETURNS JSON
READS SQL DATA
BEGIN
    RETURN (
        SELECT JSON_OBJECT('id', u.id, 'avatar_url', u.avatar_url,
                           'nick_name', u.nick_name, 'user_type', u.user_type)
        FROM `user` u WHERE u.open_id = in_id LIMIT 1
    );
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 3. 函数: fn_lessons_next_two_weeks —— 某日某类型课程列表 (返回 JSON 数组)
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS fn_lessons_next_two_weeks;
DELIMITER $$
CREATE FUNCTION fn_lessons_next_two_weeks(in_date_time INT, in_open_id TEXT, in_class_type INT)
RETURNS JSON
READS SQL DATA
BEGIN
    RETURN (
        SELECT JSON_ARRAYAGG(JSON_OBJECT(
            'course_id', t.course_id,
            'peoples', t.peoples,
            'count', t.cnt,
            'reservation_id', t.reservation_id,
            'start_time', t.start_time,
            'end_time', t.end_time,
            'date_time', t.date_time,
            'lesson_name', t.lesson_name,
            'teacher_name', t.teacher_name,
            'thumbnail', t.thumbnail,
            'hidden', t.hidden
        ))
        FROM (
            SELECT course.id AS course_id,
                   course.peoples,
                   (SELECT COUNT(reservation.id) FROM reservation
                    WHERE reservation.course_id = course.id) AS cnt,
                   (SELECT reservation.id FROM reservation
                    JOIN `user` u ON u.id = reservation.user_id
                    WHERE u.open_id = in_open_id
                      AND reservation.course_id = course.id LIMIT 1) AS reservation_id,
                   course.start_time, course.end_time, course.date_time,
                   l.name AS lesson_name, c.name AS teacher_name,
                   c.thumbnail, course.hidden
            FROM course
            JOIN coach c ON course.teacher_id = c.id
            JOIN lesson l ON l.id = course.lesson_id
            WHERE COALESCE(course.hidden, 0) <> 1
              AND course.date_time = in_date_time
              AND course.class_type & in_class_type = course.class_type
        ) t
    );
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 4. 函数: fn_book —— 预约课程 (返回预约id, 失败返回负数)
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS fn_book;
DELIMITER $$
CREATE FUNCTION fn_book(in_course_id INT, in_open_id TEXT)
RETURNS INT
MODIFIES SQL DATA
BEGIN
    DECLARE v_user_id INT DEFAULT 0;
    DECLARE v_user_type INT DEFAULT 0;
    DECLARE v_result INT DEFAULT 0;
    DECLARE v_now BIGINT DEFAULT 0;

    SELECT id, COALESCE(user_type, 0) INTO v_user_id, v_user_type
    FROM `user` WHERE open_id = in_open_id LIMIT 1;
    IF v_user_id = 0 THEN
        RETURN -200; -- 用户不存在(未注册)
    END IF;
    IF v_user_type = -1 THEN
        RETURN -100; -- 学员被屏蔽
    END IF;

    SET v_now = UNIX_TIMESTAMP();
    INSERT INTO reservation (course_id, fulfill, user_id, creation_time, updated_time, vc_id)
    VALUES (in_course_id, 0, v_user_id, v_now, v_now, 0);
    SET v_result = LAST_INSERT_ID();
    RETURN v_result;
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 5. 函数: fn_unbook —— 取消预约(仅本人) (返回被删id, 0=失败)
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS fn_unbook;
DELIMITER $$
CREATE FUNCTION fn_unbook(in_reservation_id INT, in_open_id TEXT)
RETURNS INT
MODIFIES SQL DATA
BEGIN
    DECLARE v_cnt INT DEFAULT 0;
    DELETE FROM reservation
    WHERE id = in_reservation_id
      AND user_id = (SELECT id FROM `user` WHERE open_id = in_open_id LIMIT 1);
    SET v_cnt = ROW_COUNT();
    IF v_cnt > 0 THEN
        RETURN in_reservation_id;
    END IF;
    RETURN 0;
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 6. 函数: fn_index —— 首页聚合数据 (返回 JSON)
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS fn_index;
DELIMITER $$
CREATE FUNCTION fn_index()
RETURNS JSON
READS SQL DATA
BEGIN
    RETURN (
        SELECT JSON_OBJECT(
            'poster', (SELECT JSON_ARRAYAGG(JSON_OBJECT('id', t.id, 'image', t.image))
                       FROM (SELECT id, image FROM slideshow) t),
            'booked', (SELECT JSON_ARRAYAGG(JSON_OBJECT('id', t.id, 'nick_name', t.nick_name, 'avatar_url', t.avatar_url))
                       FROM (SELECT reservation.id AS id, u.nick_name, u.avatar_url
                             FROM reservation JOIN `user` u ON u.id = reservation.user_id
                             WHERE reservation.id IN (SELECT MAX(reservation.id) FROM reservation GROUP BY reservation.user_id)
                             ORDER BY reservation.creation_time DESC LIMIT 10) t),
            'actions', (SELECT JSON_ARRAYAGG(JSON_OBJECT('id', t.id, 'name', t.name, 'image', t.image))
                        FROM (SELECT id, name, image FROM `function` ORDER BY id) t),
            'teachers', (SELECT JSON_ARRAYAGG(JSON_OBJECT('id', t.id, 'name', t.name,
                                                          'thumbnail', t.thumbnail, 'introduction', t.introduction))
                         FROM (SELECT id, name, thumbnail, introduction FROM coach) t),
            'market', (SELECT JSON_OBJECT('id', t.id, 'slogan', t.slogan)
                       FROM (SELECT id, slogan FROM market ORDER BY updated_time DESC LIMIT 1) t),
            'notices', (SELECT JSON_ARRAYAGG(JSON_OBJECT('id', t.id, 'title', t.title, 'updated_time', t.updated_time))
                        FROM (SELECT id, title, updated_time FROM announcement ORDER BY updated_time DESC LIMIT 3) t)
        )
    );
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 7. 函数: fn_query_week_lessons —— 本周(周一起)团课列表 (返回 JSON 数组)
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS fn_query_week_lessons;
DELIMITER $$
CREATE FUNCTION fn_query_week_lessons()
RETURNS JSON
READS SQL DATA
BEGIN
    DECLARE v_seconds INT DEFAULT 0;
    DECLARE v_date_seconds INT DEFAULT 0;
    DECLARE v_week INT DEFAULT 0;

    SET v_seconds = UNIX_TIMESTAMP();
    SET v_date_seconds = v_seconds - (v_seconds % 86400) - 28800; -- 北京时间当天0点
    -- MySQL: DAYOFWEEK 周日=1..周六=7; 转 PG 的 dow: 周日=0
    SET v_week = DAYOFWEEK(CURDATE()) - 1;
    IF v_week = 0 THEN
        SET v_date_seconds = v_date_seconds + 86400;
    ELSE
        SET v_date_seconds = v_date_seconds - 86400 * (v_week - 1);
    END IF;

    RETURN (
        SELECT JSON_ARRAYAGG(JSON_OBJECT(
            'start_time', r.start_time, 'end_time', r.end_time,
            'date_time', r.date_time, 'lesson_name', r.lesson_name,
            'teacher_name', r.teacher_name))
        FROM (
            SELECT course.start_time, course.end_time, course.date_time,
                   l.name AS lesson_name, c.name AS teacher_name
            FROM course
            JOIN lesson l ON l.id = course.lesson_id
            JOIN coach c ON course.teacher_id = c.id
            WHERE COALESCE(course.hidden, 0) = 0
              AND course.date_time BETWEEN v_date_seconds AND v_date_seconds + 86400 * 6
              AND course.class_type = 4
            ORDER BY date_time, start_time
        ) r
    );
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 8. 函数: fn_admin_user_lessons —— 会员约课记录 (返回 JSON 数组)
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS fn_admin_user_lessons;
DELIMITER $$
CREATE FUNCTION fn_admin_user_lessons(in_id INT, in_start BIGINT, in_end BIGINT)
RETURNS JSON
READS SQL DATA
BEGIN
    RETURN (
        SELECT JSON_ARRAYAGG(JSON_OBJECT(
            'id', t.id, 'date_time', t.date_time, 'class_type', t.class_type,
            'start_time', t.start_time, 'end_time', t.end_time,
            'lesson_name', t.lesson_name, 'teacher_name', t.teacher_name,
            'thumbnail', t.thumbnail))
        FROM (
            SELECT r.id, c.date_time, c.class_type, c.start_time, c.end_time,
                   l.name AS lesson_name, c2.name AS teacher_name, c2.thumbnail
            FROM reservation r
            JOIN `user` u ON u.id = r.user_id
            JOIN course c ON c.id = r.course_id
            JOIN lesson l ON l.id = c.lesson_id
            JOIN coach c2 ON c.teacher_id = c2.id
            WHERE u.id = in_id
              AND (in_start = 0 OR (c.date_time >= in_start AND c.date_time < in_end))
            ORDER BY c.date_time DESC
        ) t
    );
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 9. 函数: fn_admin_lessons_update —— 按星期批量排课 (返回生成条数)
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS fn_admin_lessons_update;
DELIMITER $$
CREATE FUNCTION fn_admin_lessons_update(obj JSON)
RETURNS INT
MODIFIES SQL DATA
BEGIN
    DECLARE v_class_type INT DEFAULT 4;
    DECLARE v_start_time INT DEFAULT 0;
    DECLARE v_end_time INT DEFAULT 0;
    DECLARE v_peoples INT DEFAULT 0;
    DECLARE v_week INT DEFAULT 0;
    DECLARE v_lesson_id INT DEFAULT 0;
    DECLARE v_teacher_id INT DEFAULT 0;
    DECLARE v_created INT DEFAULT 0;
    DECLARE v_now BIGINT DEFAULT 0;
    DECLARE v_target_dow INT DEFAULT 0;
    DECLARE v_offset INT DEFAULT 0;
    DECLARE d DATE;

    SET v_class_type = COALESCE(NULLIF(CAST(obj->>'$.class_type' AS SIGNED), 0), 4);
    SET v_start_time = COALESCE(NULLIF(CAST(obj->>'$.start_time' AS SIGNED), 0), 0);
    SET v_end_time   = COALESCE(NULLIF(CAST(obj->>'$.end_time' AS SIGNED), 0), 0);
    SET v_peoples    = COALESCE(NULLIF(CAST(obj->>'$.peoples' AS SIGNED), 0), 0);
    SET v_week       = COALESCE(NULLIF(CAST(obj->>'$.date_time' AS SIGNED), 0), 0);

    SELECT id INTO v_lesson_id FROM lesson WHERE name = obj->>'$.lesson' LIMIT 1;
    IF v_lesson_id = 0 THEN
        RETURN -1;
    END IF;
    SELECT id INTO v_teacher_id FROM coach WHERE name = obj->>'$.teacher' LIMIT 1;
    IF v_teacher_id = 0 THEN
        RETURN -1;
    END IF;

    -- PG dow(周日=0) → MySQL DAYOFWEEK(周日=1)
    SET v_target_dow = v_week + 1;
    SET v_offset = (v_target_dow - DAYOFWEEK(CURDATE()) + 7) % 7;
    SET d = CURDATE() + INTERVAL v_offset DAY;
    SET v_now = UNIX_TIMESTAMP();

    WHILE d <= CURDATE() + INTERVAL 1 YEAR DO
        INSERT INTO course (class_type, lesson_id, date_time, end_time, peoples,
                            start_time, teacher_id, creation_time, updated_time)
        VALUES (v_class_type, v_lesson_id, UNIX_TIMESTAMP(d) - 28800, v_end_time, v_peoples,
                v_start_time, v_teacher_id, v_now, v_now);
        SET v_created = v_created + 1;
        SET d = d + INTERVAL 7 DAY;
    END WHILE;
    RETURN v_created;
END$$
DELIMITER ;

-- ------------------------------------------------------------
-- 10. 初始化基础数据（可选，执行后小程序首页即有内容）
-- ------------------------------------------------------------
-- INSERT INTO coach (name, thumbnail, introduction) VALUES
-- ('王教练', '', '高级瑜伽教练');
-- INSERT INTO lesson (name) VALUES ('哈他瑜伽'), ('流瑜伽'), ('阴瑜伽');
-- INSERT INTO `function` (id, name, image) VALUES
-- (2, '约课', ''), (3, '私教', ''), (5, '数独', ''), (6, '商城', ''), (7, '公告', '');
-- INSERT INTO market (title, slogan, content, updated_time)
-- VALUES ('会员卡', '办卡优惠', '# 咨询前台办卡', UNIX_TIMESTAMP());

<?php
require '/var/www/lib/session.php';
session_destroy();
header('Location: /');
exit;

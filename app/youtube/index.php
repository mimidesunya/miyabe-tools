<?php
declare(strict_types=1);
// Deploy only after tatsuhiko.miya.be is ready. Never forward an upload body.
header('Cache-Control: no-store');
header('Location: https://tatsuhiko.miya.be/youtube/', true, 303);
exit;

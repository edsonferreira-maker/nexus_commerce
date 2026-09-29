import 'package:flutter/material.dart';
import 'package:flutter_web_plugins/url_strategy.dart';

import 'nexo_app.dart';

void main() {
  usePathUrlStrategy();
  runApp(const NexoApp());
}

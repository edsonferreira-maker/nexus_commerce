// ignore_for_file: avoid_web_libraries_in_flutter, deprecated_member_use

import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;

Future<String?> captureProductHtml(String url) async {
  final host = Uri.tryParse(url)?.host.toLowerCase() ?? '';
  if (!(host == 'mercadolivre.com.br' ||
      host.endsWith('.mercadolivre.com.br') ||
      host == 'mercadolibre.com.br' ||
      host.endsWith('.mercadolibre.com.br'))) {
    return null;
  }
  final requestId =
      '${DateTime.now().microsecondsSinceEpoch}-${url.hashCode.abs()}';
  final result = Completer<String?>();
  var acknowledged = false;
  Timer? responseTimer;
  late StreamSubscription<html.MessageEvent> subscription;

  void finish(String? value) {
    if (result.isCompleted) return;
    responseTimer?.cancel();
    subscription.cancel();
    result.complete(value);
  }

  subscription = html.window.onMessage.listen((event) {
    if (event.origin != html.window.location.origin) return;
    final raw = event.data;
    if (raw is! String) return;
    Map<String, dynamic> data;
    try {
      data = jsonDecode(raw) as Map<String, dynamic>;
    } catch (_) {
      return;
    }
    if (data['source'] != 'nexo-browser-extension') return;
    if (data['requestId'] != requestId) return;
    if (data['type'] == 'capture-ack') {
      acknowledged = true;
      responseTimer?.cancel();
      responseTimer = Timer(const Duration(seconds: 25), () => finish(null));
      return;
    }
    if (data['type'] == 'capture-result') {
      final captured = data['html'];
      finish(captured is String && captured.length >= 500 ? captured : null);
    }
  });

  responseTimer = Timer(const Duration(milliseconds: 450), () {
    if (!acknowledged) finish(null);
  });
  html.window.postMessage(jsonEncode({
    'source': 'nexo-commerce',
    'type': 'capture-product',
    'requestId': requestId,
    'url': url,
  }), html.window.location.origin);
  return result.future;
}

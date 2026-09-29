import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

const _configuredEndpoint = String.fromEnvironment('TRAFFIC_ENDPOINT');
const _selfHosted = bool.fromEnvironment('NEXO_SELF_HOSTED');
final _endpoint = _configuredEndpoint.isNotEmpty
    ? _configuredEndpoint
    : _selfHosted
    ? '/api/traffic/live'
    : '';
const _background = Color(0xFF101B2D);
const _border = Color(0xFF25334A);
const _muted = Color(0xFF8D9AAF);
const _green = Color(0xFF72E59B);

class TrafficCity {
  const TrafficCity(
    this.name,
    this.state,
    this.latitude,
    this.longitude,
    this.visitors,
  );

  final String name, state;
  final double latitude, longitude;
  final int visitors;

  factory TrafficCity.fromJson(Map<String, dynamic> json) {
    final name = json['city'];
    final state = json['state'];
    final latitude = json['lat'];
    final longitude = json['lon'];
    final visitors = json['visitors'];
    if (name is! String ||
        name.isEmpty ||
        state is! String ||
        latitude is! num ||
        longitude is! num ||
        visitors is! int ||
        visitors < 0) {
      throw const FormatException('Cidade de tráfego inválida');
    }
    if (latitude < -34 || latitude > 6 || longitude < -74 || longitude > -34) {
      throw const FormatException('Coordenadas fora do Brasil');
    }
    return TrafficCity(
      name,
      state,
      latitude.toDouble(),
      longitude.toDouble(),
      visitors,
    );
  }
}

class TrafficSnapshot {
  const TrafficSnapshot(
    this.activeVisitors,
    this.cities, {
    this.unlocatedVisitors = 0,
    this.geoipReady = true,
    this.hourlyViews = const [],
    this.views24h = 0,
  });
  final int activeVisitors;
  final List<TrafficCity> cities;
  final int unlocatedVisitors;
  final bool geoipReady;
  final List<int> hourlyViews;
  final int views24h;

  factory TrafficSnapshot.fromJson(Map<String, dynamic> json) {
    final total = json['activeVisitors'];
    final rawCities = json['cities'];
    if (total is! int || total < 0 || rawCities is! List) {
      throw const FormatException('Resposta de tráfego inválida');
    }
    final cities =
        rawCities
            .take(100)
            .map(
              (entry) =>
                  TrafficCity.fromJson(Map<String, dynamic>.from(entry as Map)),
            )
            .toList()
          ..sort((a, b) => b.visitors.compareTo(a.visitors));
    final unlocated = json['unlocatedVisitors'];
    final rawHours = json['hourlyViews'];
    final hours = rawHours is List && rawHours.length == 24
        ? rawHours
              .map((value) => value is int && value >= 0 ? value : 0)
              .toList()
        : <int>[];
    return TrafficSnapshot(
      total,
      cities,
      unlocatedVisitors: unlocated is int ? unlocated : 0,
      geoipReady: json['geoipReady'] != false,
      hourlyViews: hours,
      views24h: json['views24h'] is int
          ? json['views24h'] as int
          : hours.fold(0, (sum, value) => sum + value),
    );
  }
}

const _sample = TrafficSnapshot(
  184,
  [
    TrafficCity('São Paulo', 'SP', -23.5505, -46.6333, 64),
    TrafficCity('Rio de Janeiro', 'RJ', -22.9068, -43.1729, 38),
    TrafficCity('Belo Horizonte', 'MG', -19.9167, -43.9345, 27),
    TrafficCity('Brasília', 'DF', -15.7939, -47.8828, 20),
    TrafficCity('Salvador', 'BA', -12.9777, -38.5016, 19),
    TrafficCity('Curitiba', 'PR', -25.4284, -49.2733, 16),
  ],
  hourlyViews: [
    2,
    1,
    0,
    0,
    1,
    2,
    4,
    7,
    12,
    18,
    22,
    26,
    24,
    19,
    21,
    27,
    30,
    28,
    23,
    18,
    14,
    12,
    9,
    6,
  ],
  views24h: 326,
);

const _empty = TrafficSnapshot(0, []);

class TrafficMap extends StatefulWidget {
  const TrafficMap({super.key});

  @override
  State<TrafficMap> createState() => _TrafficMapState();
}

class _TrafficMapState extends State<TrafficMap> {
  TrafficSnapshot? snapshot;
  TrafficCity? selected;
  DateTime? refreshedAt;
  Timer? timer;
  bool loading = false;
  String? error;

  @override
  void initState() {
    super.initState();
    if (_endpoint.isNotEmpty) {
      refresh();
      timer = Timer.periodic(const Duration(seconds: 15), (_) => refresh());
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> refresh() async {
    if (loading || _endpoint.isEmpty) return;
    setState(() => loading = true);
    try {
      final response = await http
          .get(Uri.base.resolve(_endpoint))
          .timeout(const Duration(seconds: 8));
      if (response.statusCode != 200) {
        throw Exception('HTTP ${response.statusCode}');
      }
      final data = TrafficSnapshot.fromJson(
        Map<String, dynamic>.from(jsonDecode(response.body) as Map),
      );
      if (!mounted) return;
      setState(() {
        snapshot = data;
        refreshedAt = DateTime.now();
        error = null;
        selected = null;
      });
    } catch (_) {
      if (mounted) {
        setState(() => error = 'Não foi possível atualizar o tráfego.');
      }
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = snapshot ?? (_endpoint.isEmpty ? _sample : _empty);
    final isSample = snapshot == null && _endpoint.isEmpty;
    final city = selected;
    return Card(
      color: _background,
      child: Padding(
        padding: const EdgeInsets.all(22),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 18,
              runSpacing: 12,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                const Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Tráfego no Brasil',
                      style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    SizedBox(height: 4),
                    Text(
                      'Visitantes ativos por cidade',
                      style: TextStyle(color: _muted, fontSize: 12),
                    ),
                  ],
                ),
                _status(isSample),
                if (_endpoint.isNotEmpty)
                  IconButton(
                    tooltip: 'Atualizar tráfego',
                    onPressed: loading ? null : refresh,
                    icon: loading
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.refresh),
                  ),
              ],
            ),
            const SizedBox(height: 14),
            if (snapshot == null)
              Text(
                _endpoint.isEmpty
                    ? 'Amostra visual. Conecte um endpoint de eventos para acompanhar visitantes reais.'
                    : 'Aguardando dados reais do servidor.',
                style: const TextStyle(color: _muted, fontSize: 12),
              ),
            if (error != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  error!,
                  style: const TextStyle(
                    color: Color(0xFFFF7885),
                    fontSize: 12,
                  ),
                ),
              ),
            LayoutBuilder(
              builder: (_, constraints) => constraints.maxWidth < 760
                  ? Column(
                      children: [
                        _map(data),
                        const SizedBox(height: 12),
                        _summary(data, city),
                      ],
                    )
                  : Row(
                      crossAxisAlignment: CrossAxisAlignment.center,
                      children: [
                        Expanded(flex: 3, child: _map(data)),
                        const SizedBox(width: 24),
                        Expanded(flex: 2, child: _summary(data, city)),
                      ],
                    ),
            ),
            const SizedBox(height: 14),
            const Divider(color: _border),
            const SizedBox(height: 12),
            Row(
              children: [
                const Expanded(
                  child: Text(
                    'Visitas nas últimas 24 horas',
                    style: TextStyle(fontWeight: FontWeight.w800),
                  ),
                ),
                Text(
                  '${data.views24h} visitas',
                  style: const TextStyle(
                    color: _green,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              height: 120,
              child: CustomPaint(painter: TrafficBarsPainter(data.hourlyViews)),
            ),
            const Row(
              children: [
                Text(
                  '24h atrás',
                  style: TextStyle(color: _muted, fontSize: 10),
                ),
                Spacer(),
                Text('Agora', style: TextStyle(color: _muted, fontSize: 10)),
              ],
            ),
            Wrap(
              spacing: 14,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                const Text(
                  'Malha geográfica: IBGE',
                  style: TextStyle(color: _muted, fontSize: 10),
                ),
                TextButton(
                  onPressed: () => launchUrl(Uri.parse('https://db-ip.com')),
                  child: const Text(
                    'Geolocalização de IP: DB-IP',
                    style: TextStyle(fontSize: 10),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _status(bool isSample) {
    final time = refreshedAt == null
        ? ''
        : '${refreshedAt!.hour.toString().padLeft(2, '0')}:${refreshedAt!.minute.toString().padLeft(2, '0')}:${refreshedAt!.second.toString().padLeft(2, '0')}';
    final label = snapshot == null
        ? isSample
              ? 'DEMONSTRAÇÃO'
              : error == null
              ? 'CONECTANDO'
              : 'SEM CONEXÃO'
        : error == null
        ? 'AO VIVO • $time'
        : 'ÚLTIMA LEITURA • $time';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: _green.withValues(alpha: .12),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: const BoxDecoration(
              color: _green,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 7),
          Text(
            label,
            style: const TextStyle(
              color: _green,
              fontSize: 10,
              fontWeight: FontWeight.w800,
            ),
          ),
        ],
      ),
    );
  }

  Widget _map(TrafficSnapshot data) => Center(
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 460),
      child: AspectRatio(
        aspectRatio: 1,
        child: LayoutBuilder(
          builder: (_, constraints) {
            final side = constraints.maxWidth;
            return Stack(
              children: [
                Positioned.fill(
                  child: SvgPicture.asset(
                    'assets/maps/brasil_uf_ibge.svg',
                    fit: BoxFit.fill,
                  ),
                ),
                ...data.cities.take(25).map((city) {
                  final x = (city.longitude + 73.9833) / 39.1806;
                  final y = (5.2718 - city.latitude) / 39.0157;
                  final radius = math.max(
                    7.0,
                    math.min(16.0, 6 + city.visitors / 7),
                  );
                  return Positioned(
                    left: side * x - radius,
                    top: side * y - radius,
                    child: Tooltip(
                      message:
                          '${_cityLabel(city)}: ${city.visitors} visitantes',
                      child: InkWell(
                        onTap: () => setState(() => selected = city),
                        customBorder: const CircleBorder(),
                        child: Container(
                          width: radius * 2,
                          height: radius * 2,
                          decoration: BoxDecoration(
                            color: _green.withValues(alpha: .75),
                            shape: BoxShape.circle,
                            border: Border.all(color: Colors.white, width: 2),
                            boxShadow: [
                              BoxShadow(
                                color: _green.withValues(alpha: .3),
                                blurRadius: 12,
                                spreadRadius: 4,
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  );
                }),
              ],
            );
          },
        ),
      ),
    ),
  );

  Widget _summary(TrafficSnapshot data, TrafficCity? city) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        '${data.activeVisitors}',
        style: const TextStyle(
          fontSize: 44,
          fontWeight: FontWeight.w900,
          color: _green,
        ),
      ),
      const Text(
        'visitantes ativos no Brasil',
        style: TextStyle(color: _muted, fontSize: 12),
      ),
      if (data.unlocatedVisitors > 0) ...[
        const SizedBox(height: 5),
        Text(
          '${data.unlocatedVisitors} visitante(s) sem localização',
          style: const TextStyle(color: _muted, fontSize: 11),
        ),
      ],
      if (!data.geoipReady) ...[
        const SizedBox(height: 5),
        const Text(
          'Base geográfica em preparação.',
          style: TextStyle(color: _muted, fontSize: 11),
        ),
      ],
      if (city != null) ...[
        const SizedBox(height: 14),
        Text(
          '${_cityLabel(city)}: ${city.visitors} visitantes',
          style: const TextStyle(fontWeight: FontWeight.w800),
        ),
      ],
      const SizedBox(height: 22),
      const Text(
        'Cidades com mais acessos',
        style: TextStyle(fontWeight: FontWeight.w800),
      ),
      const SizedBox(height: 12),
      if (data.cities.isEmpty)
        const Text(
          'Nenhum visitante ativo por cidade.',
          style: TextStyle(color: _muted),
        )
      else
        ...data.cities
            .take(5)
            .map(
              (entry) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Row(
                  children: [
                    const Icon(
                      Icons.location_on_outlined,
                      color: _green,
                      size: 17,
                    ),
                    const SizedBox(width: 7),
                    Expanded(
                      child: Text(
                        _cityLabel(entry),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    Text(
                      '${entry.visitors}',
                      style: const TextStyle(fontWeight: FontWeight.w800),
                    ),
                  ],
                ),
              ),
            ),
      const SizedBox(height: 8),
      const Divider(color: _border),
      const Text(
        'Atualização automática a cada 15 segundos quando conectado.',
        style: TextStyle(color: _muted, fontSize: 11),
      ),
    ],
  );
}

String _cityLabel(TrafficCity city) =>
    city.state.isEmpty ? city.name : '${city.name}, ${city.state}';

class TrafficBarsPainter extends CustomPainter {
  const TrafficBarsPainter(this.values);
  final List<int> values;

  @override
  void paint(Canvas canvas, Size size) {
    final grid = Paint()..color = _border;
    for (var i = 0; i < 4; i++) {
      final y = size.height * i / 3;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }
    if (values.isEmpty) return;
    final peak = math.max(1, values.reduce(math.max));
    final slot = size.width / values.length;
    final bar = math.max(2.0, slot - 4);
    final paint = Paint()..color = _green;
    for (var i = 0; i < values.length; i++) {
      final height = size.height * values[i] / peak;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(
            slot * i + (slot - bar) / 2,
            size.height - height,
            bar,
            height,
          ),
          const Radius.circular(3),
        ),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant TrafficBarsPainter oldDelegate) =>
      oldDelegate.values != values;
}

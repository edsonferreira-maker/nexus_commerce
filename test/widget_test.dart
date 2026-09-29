import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nexo_commerce/nexo_app.dart';
import 'package:nexo_commerce/traffic_map.dart';

void main() {
  testWidgets('abre o dashboard web da Nexo', (tester) async {
    await tester.pumpWidget(const NexoApp());
    expect(find.text('Olá, Edson 👋'), findsOneWidget);
    expect(find.text('Faturamento'), findsOneWidget);
  });

  testWidgets('navega para o catálogo', (tester) async {
    await tester.pumpWidget(const NexoApp());
    await tester.tap(find.text('Produtos').first);
    await tester.pumpAndSettle();
    expect(find.text('Seus produtos'), findsOneWidget);
  });

  testWidgets('mostra a página completa antes de publicar', (tester) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(const NexoApp());
    await tester.tap(find.text('Importar produto').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Importar informações'));
    await tester.pump(const Duration(milliseconds: 900));
    await tester.pumpAndSettle();

    expect(find.text('Ver página completa'), findsOneWidget);
    await tester.tap(find.text('Ver página completa'));
    await tester.pumpAndSettle();

    expect(
      find.text('PRÉVIA DA PÁGINA • conteúdo demonstrativo'),
      findsOneWidget,
    );
    expect(find.text('Sobre o produto'), findsOneWidget);
    expect(find.textContaining('Avaliações de clientes'), findsOneWidget);
    expect(find.text('Mariana S.'), findsOneWidget);
    expect(find.byType(Image), findsWidgets);

    await tester.tap(find.byIcon(Icons.arrow_back).last);
    await tester.pumpAndSettle();
    expect(find.text('Publicar página'), findsOneWidget);
  });

  testWidgets('exibe o mapa do Brasil junto aos gráficos em Analytics', (
    tester,
  ) async {
    await tester.pumpWidget(const NexoApp());
    await tester.tap(find.text('Analytics').first);
    await tester.pumpAndSettle();
    expect(find.byType(TrafficMap), findsOneWidget);
    expect(find.text('Tráfego no Brasil'), findsOneWidget);
    expect(find.text('DEMONSTRAÇÃO'), findsOneWidget);
    expect(find.text('Receita por canal'), findsOneWidget);
  });

  testWidgets('a prévia e o mapa cabem em tela móvel', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      const MaterialApp(home: Storefront(isPreview: true)),
    );
    await tester.pumpAndSettle();
    expect(find.text('Sobre o produto'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const NexoApp());
    await tester.tap(find.text('Analytics').first);
    await tester.pumpAndSettle();
    expect(find.byType(TrafficMap), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  test('interpreta o contrato de tráfego por cidade', () {
    final snapshot = TrafficSnapshot.fromJson({
      'activeVisitors': 3,
      'cities': [
        {
          'city': 'Recife',
          'state': 'PE',
          'lat': -8.05,
          'lon': -34.9,
          'visitors': 3,
        },
      ],
    });
    expect(snapshot.activeVisitors, 3);
    expect(snapshot.cities.single.name, 'Recife');
  });

  test('usa fotos e comentários recebidos no produto importado', () {
    final product = ProductPageData.fromJson({
      'id': 'MLB1234567890',
      'slug': 'mlb1234567890',
      'name': 'Produto real',
      'price': 'R\$ 99,90',
      'images': ['/media/mlb1234567890-0.jpg', '/media/mlb1234567890-1.jpg'],
      'description': 'Descrição do anúncio',
      'reviews': [
        {
          'author': 'Comprador',
          'date': '01/09/2026',
          'comment': 'Ótimo',
          'stars': 5,
        },
      ],
    });
    expect(product.images.length, 2);
    expect(product.reviews.single.comment, 'Ótimo');
    expect(product.description, 'Descrição do anúncio');
  });
}

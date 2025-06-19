document.addEventListener('DOMContentLoaded', function() {
  const category = document.getElementById('category');
  const manufacturer = document.getElementById('manufacturer');
  const color = document.getElementById('color');
  const results = document.getElementById('search-results');

  // Селекты для динамического обновления
  function updateSelects() {
    // Если категория не выбрана
    if (!category.value || category.value === "all") {
      manufacturer.value = "all";
      manufacturer.disabled = true;
      color.value = "all";
      color.disabled = true;
    } else {
      manufacturer.disabled = false;
      // Если производитель не выбран
      if (!manufacturer.value || manufacturer.value === "all") {
        color.value = "all";
        color.disabled = true;
      } else {
        color.disabled = false;
      }
    }
  }

  // Сам запрос и вывод результата
  function doSearch() {
    updateSelects();
    const params = new URLSearchParams({
      category_id: category.value || "all",
      manufacturer_id: manufacturer.value || "all",
      color_id: color.value || "all"
    });
    fetch(`/api/auto_search?${params.toString()}`)
      .then(r => r.text())
      .then(html => {
        results.innerHTML = html;
      });
  }

  // Автоматически подгружаем производителей и цвета при изменении
  function updateManufacturersAndColors() {
    const cat = category.value || "all";
    const man = manufacturer.value || "all";
    fetch(`/api/filter_options?category_id=${cat}&manufacturer_id=${man}`)
      .then(r => r.json())
      .then(data => {
        // Обновляем manufacturer
        const manValue = manufacturer.value;
        manufacturer.innerHTML = '<option value="all">Все</option>';
        data.manufacturers.forEach(man => {
          let opt = document.createElement('option');
          opt.value = man.manufacturer_id;
          opt.text = man.name;
          if (manValue == String(man.manufacturer_id)) opt.selected = true;
          manufacturer.appendChild(opt);
        });

        // Обновляем color
        const colorValue = color.value;
        color.innerHTML = '<option value="all">Все</option>';
        data.colors.forEach(col => {
          let opt = document.createElement('option');
          opt.value = col.color_id;
          opt.text = col.name;
          if (colorValue == String(col.color_id)) opt.selected = true;
          color.appendChild(opt);
        });

        updateSelects();
      });
  }

  // Слушатели событий
  category.addEventListener('change', () => {
    updateManufacturersAndColors();
    manufacturer.value = "all";
    color.value = "all";
    doSearch();
  });
  manufacturer.addEventListener('change', () => {
    updateManufacturersAndColors();
    color.value = "all";
    doSearch();
  });
  color.addEventListener('change', doSearch);

  // Инициализация
  updateManufacturersAndColors();
  doSearch();
});

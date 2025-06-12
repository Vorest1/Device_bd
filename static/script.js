document.addEventListener('DOMContentLoaded', function() {
  const category = document.getElementById('category');
  const manufacturer = document.getElementById('manufacturer');
  const color = document.querySelector('[name="color_id"]');

  // Функция обновления производителей и цветов при изменении выбора
  function updateFilters() {
    let cat = category ? category.value : "all";
    let man = manufacturer ? manufacturer.value : "all";
    fetch(`/api/filter_options?category_id=${cat}&manufacturer_id=${man}`)
      .then(r => r.json())
      .then(data => {
        // --- Производители ---
        if (manufacturer) {
          let selected = manufacturer.value;
          manufacturer.innerHTML = '<option value="all">Все</option>';
          data.manufacturers.forEach(function(man) {
            let opt = document.createElement('option');
            opt.value = man.manufacturer_id;
            opt.text = man.name;
            if (selected == String(man.manufacturer_id)) opt.selected = true;
            manufacturer.appendChild(opt);
          });
        }
        // --- Цвета ---
        if (color) {
          let selected = color.value;
          color.innerHTML = '<option value="all">Все</option>';
          data.colors.forEach(function(col) {
            let opt = document.createElement('option');
            opt.value = col.color_id;
            opt.text = col.name;
            if (selected == String(col.color_id)) opt.selected = true;
            color.appendChild(opt);
          });
        }
      });
  }

  if (category) category.addEventListener('change', updateFilters);
  if (manufacturer) manufacturer.addEventListener('change', updateFilters);

  // Для корректного восстановления после POST — обновляем один раз при загрузке, если уже выбран фильтр
  updateFilters();
});
